"""Local CPU-friendly LoRA trainer for flywheel datasets (transformers + PEFT).

The trainer turns every dataset kind into a text-generation objective, so a
single code path fine-tunes the classify (preflight), instruct (finding draft)
and preference (negative sampling) datasets. Transformers/PEFT are imported
lazily so the rest of AegisAgent does not depend on torch being installed.
"""
import csv
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from .hub import cached_model_path

DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-Coder-0.5B-Instruct"


def default_hyperparams(env=None) -> Dict[str, Any]:
    env = env or os.environ
    return {
        "epochs": int(env.get("AEGIS_FTW_EPOCHS", "3")),
        "learning_rate": float(env.get("AEGIS_FTW_LR", "2e-4")),
        "lora_r": int(env.get("AEGIS_FTW_R", "8")),
        "lora_alpha": int(env.get("AEGIS_FTW_ALPHA", "16")),
        "lora_dropout": float(env.get("AEGIS_FTW_DROPOUT", "0.05")),
        "max_seq_len": int(env.get("AEGIS_FTW_MAX_SEQ", "512")),
        "batch_size": int(env.get("AEGIS_FTW_BATCH", "2")),
        "grad_accum": int(env.get("AEGIS_FTW_GRAD_ACCUM", "8")),
        "val_ratio": float(env.get("AEGIS_FTW_VAL_RATIO", "0.1")),
        "max_train_examples": int(env.get("AEGIS_FTW_SAMPLE_BUDGET", "800")),
        "eval_examples": int(env.get("AEGIS_FTW_EVAL_EXAMPLES", "40")),
    }


def base_model_name(env=None) -> str:
    return (env or os.environ).get("AEGIS_FTW_BASE_MODEL", DEFAULT_BASE_MODEL)


def label_json(row: Dict[str, Any]) -> str:
    payload = row.get("payload") or {}
    kind = str(row.get("kind") or payload.get("kind") or "classify")
    if kind == "classify":
        return json.dumps(payload.get("response") or {"keep": False},
                          ensure_ascii=False)
    if kind == "instruct":
        return json.dumps(payload.get("response") or {}, ensure_ascii=False)
    if kind == "episode":
        return json.dumps(payload.get("response") or {}, ensure_ascii=False)
    # preference: ask the model to emit the better finding.
    return json.dumps(payload.get("chosen") or {}, ensure_ascii=False)


def prompt_text(row: Dict[str, Any]) -> str:
    payload = row.get("payload") or {}
    kind = str(row.get("kind") or payload.get("kind") or "classify")
    if kind == "classify":
        user = payload.get("input") or {}
        return (
            "You are a risk preflight classifier for LLM/AI application code. "
            "Given one changed code line, return JSON {\"keep\": true|false, "
            "\"risk_class\": \"...\", \"severity\": \"...\"}. Risk classes: "
            "prompt-injection, tool-escape, data-leak-rag, agent-loop-abuse, "
            "llm-misuse, classic-sec.\n\nInput:\n"
            + json.dumps(user, ensure_ascii=False)
        )
    if kind == "instruct":
        prompt = payload.get("prompt") or {}
        return "%s\n\n%s" % (
            prompt.get("system", ""), prompt.get("user", "")
        )
    if kind == "episode":
        input_data = payload.get("input") or {}
        context = input_data.get("context") or {}
        return (
            "You are reviewing a full code-review episode. Using the recorded "
            "steps and the worker turn context, emit the final accepted findings "
            "JSON.\n\nEpisode steps:\n"
            + json.dumps(input_data.get("steps") or [], ensure_ascii=False)
            + "\n\nWorker turn (system):\n" + str(context.get("system") or "")
            + "\n\nWorker turn (task):\n" + str(context.get("user") or "")
        )
    input_data = payload.get("input") or {}
    chosen = payload.get("chosen") or {}
    rejected = payload.get("rejected") or {}
    return (
        "%s\n\nChanged code:\n%s\n\nA finding:\n%s\n\nB finding:\n%s"
        % (
            input_data.get("instruction", ""),
            json.dumps(payload.get("code_input") or {}, ensure_ascii=False),
            json.dumps(chosen, ensure_ascii=False),
            json.dumps(rejected, ensure_ascii=False),
        )
    )


def train_from_rows(
    rows: List[Dict[str, Any]], hyper: Optional[Dict[str, Any]] = None,
    base_model: str = "", out_root: str = "", run_id: str = "",
) -> Dict[str, Any]:
    """Run a real LoRA training loop on CPU/GPU and persist an adapter.

    Returns a dict with run_id, adapter_dir, and metrics; raises a clear error
    when the training stack (torch / transformers / peft) is not installed.
    """
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, get_peft_model
    except ImportError as exc:
        raise RuntimeError(
            "flywheel training requires torch, transformers and peft "
            "(pip install torch transformers peft safetensors accelerate); "
            "missing: %s" % exc
        ) from exc

    hyper = dict(hyper or default_hyperparams())
    base_model = base_model or base_model_name()
    run_id = run_id or str(uuid.uuid4())
    out_root = out_root or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
        "artifacts", "adapters",
    )
    adapter_dir = os.path.join(out_root, run_id)
    os.makedirs(adapter_dir, exist_ok=True)

    texts = [prompt_text(row) for row in rows]
    labels = [label_json(row) for row in rows]
    budget = int(hyper.get("max_train_examples", 800))
    if budget and len(texts) > budget:
        texts, labels = texts[:budget], labels[:budget]
    if not texts:
        raise ValueError("no training samples provided")

    val_count = max(1, int(len(texts) * float(hyper.get("val_ratio", 0.1))))
    eval_examples = max(1, min(int(hyper.get("eval_examples", 40)), val_count))
    train_texts, train_labels = texts[val_count:], labels[val_count:]
    val_texts, val_labels = texts[:val_count], labels[:val_count]

    device = torch.device("cpu")

    def load_offline_fallback(loader, *args, **kwargs):
        # If the model is already cached locally, load straight from the cache and
        # never touch the network (HF can be unreachable / slow). Otherwise try the
        # hub first, then fall back to cache-only as a last resort.
        snapshot = cached_model_path(str(args[0]))
        if snapshot:
            return loader(snapshot, local_files_only=True, **kwargs)
        try:
            return loader(*args, **kwargs)
        except Exception:
            return loader(*args, local_files_only=True, **kwargs)

    tokenizer = load_offline_fallback(
        AutoTokenizer.from_pretrained, base_model, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = load_offline_fallback(
        AutoModelForCausalLM.from_pretrained, base_model,
        trust_remote_code=True, torch_dtype=torch.float32,
    ).to(device)
    lora = LoraConfig(
        r=int(hyper.get("lora_r", 8)), lora_alpha=int(hyper.get("lora_alpha", 16)),
        lora_dropout=float(hyper.get("lora_dropout", 0.05)),
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora)
    model.train()

    optimizer = torch.optim.AdamW(model.parameters(), lr=float(hyper.get("learning_rate", 2e-4)))
    max_seq = int(hyper.get("max_seq_len", 512))
    batch_size = int(hyper.get("batch_size", 2))
    grad_accum = int(hyper.get("grad_accum", 8))
    epochs = int(hyper.get("epochs", 3))

    csv_path = os.path.join(adapter_dir, "loss.csv")
    metrics_csv = open(csv_path, "w", newline="", encoding="utf-8")
    writer = csv.writer(metrics_csv)
    writer.writerow(["epoch", "step", "train_loss", "val_loss"])

    def collate(batch_texts: List[str], batch_labels: List[str]):
        # Causal-LM friendly: prompt + label are one sequence; the prompt prefix
        # is masked out so the model learns to emit the label (the JSON answer).
        combined = [
            "%s\n\nResponse:\n%s" % (text, label)
            for text, label in zip(batch_texts, batch_labels)
        ]
        encoded = tokenizer(
            combined, padding="max_length", truncation=True,
            max_length=max_seq, return_tensors="pt",
        )
        input_ids = encoded["input_ids"].to(device)
        attention = encoded["attention_mask"].to(device)
        prompt_lengths = [
            len(tokenizer(text, truncation=True, max_length=max_seq)["input_ids"])
            for text in batch_texts
        ]
        labels_ids = input_ids.clone()
        labels_ids[labels_ids == tokenizer.pad_token_id] = -100
        for index, length in enumerate(prompt_lengths):
            labels_ids[index, :length] = -100
        return input_ids, attention, labels_ids

    step_count = 0
    train_loss_log: List[float] = []
    val_loss = None
    started = time.monotonic()
    try:
        for epoch in range(1, epochs + 1):
            order = list(range(len(train_texts)))
            micro = 0
            loss_sum = 0.0
            for index in range(0, len(order), batch_size):
                chunk = order[index:index + batch_size]
                inputs = collate(
                    [train_texts[i] for i in chunk],
                    [train_labels[i] for i in chunk],
                )
                outputs = model(*inputs[:2], labels=inputs[2])
                outputs.loss.backward()
                loss_sum += float(outputs.loss.detach())
                micro += 1
                if micro % grad_accum == 0:
                    optimizer.step()
                    optimizer.zero_grad()
                    step_count += 1
                    avg_loss = loss_sum / grad_accum
                    loss_sum = 0.0
                    train_loss_log.append(avg_loss)
                    if step_count % 10 == 0:
                        writer.writerow([epoch, step_count, avg_loss, ""])
                        metrics_csv.flush()
            if micro % grad_accum != 0:
                optimizer.step()
                optimizer.zero_grad()
                step_count += 1
                avg_loss = loss_sum / max(1, micro % grad_accum)
                train_loss_log.append(avg_loss)
            # one validation pass per epoch
            val_total = 0.0
            val_steps = 0
            model.eval()
            with torch.no_grad():
                for index in range(0, len(val_texts), batch_size):
                    chunk = val_texts[index:index + batch_size]
                    clabels = val_labels[index:index + batch_size]
                    inputs = collate(chunk, clabels)
                    outputs = model(*inputs[:2], labels=inputs[2])
                    val_total += float(outputs.loss)
                    val_steps += 1
                    if val_steps >= eval_examples:
                        break
            model.train()
            val_loss = val_total / max(1, val_steps)
            writer.writerow([epoch, step_count, "", val_loss])
            metrics_csv.flush()
    finally:
        metrics_csv.close()

    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    elapsed_s = int(time.monotonic() - started)

    metrics = {
        "train_loss": round(sum(train_loss_log) / max(1, len(train_loss_log)), 6),
        "val_loss": None if val_loss is None else round(val_loss, 6),
        "samples": len(texts), "train_samples": len(train_texts),
        "val_samples": len(val_texts), "epochs": epochs,
        "steps": step_count, "duration_s": elapsed_s,
        "loss_csv": csv_path, "device": "cpu",
    }
    plot_path = _plot_loss(csv_path, adapter_dir)
    if plot_path:
        metrics["loss_plot"] = plot_path
    with open(os.path.join(adapter_dir, "metrics.json"), "w", encoding="utf-8") as handle:
        json.dump({**metrics, "hyperparams": hyper, "base_model": base_model},
                  handle, ensure_ascii=False, indent=2)
    return {
        "run_id": run_id, "adapter_dir": adapter_dir,
        "base_model": base_model, "metrics": metrics,
        "hyperparams": hyper,
    }


def _plot_loss(csv_path: str, adapter_dir: str) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    epochs, steps, train_loss, val_loss = [], [], [], []
    try:
        with open(csv_path, "r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row["train_loss"]:
                    steps.append(int(row["step"])); train_loss.append(float(row["train_loss"]))
                if row["val_loss"]:
                    epochs.append(int(row["epoch"])); val_loss.append(float(row["val_loss"]))
    except Exception:
        return None
    if not steps and not epochs:
        return None
    plot_path = os.path.join(adapter_dir, "loss.png")
    try:
        fig, ax = plt.subplots(figsize=(5, 3))
        if train_loss:
            ax.plot(steps, train_loss, label="train")
        if val_loss:
            ax.plot(epochs, val_loss, marker="o", label="val")
        ax.set_xlabel("step" if train_loss else "epoch")
        ax.set_ylabel("loss")
        ax.legend()
        fig.tight_layout()
        fig.savefig(plot_path, dpi=120)
        plt.close(fig)
        return plot_path
    except Exception:
        return None
