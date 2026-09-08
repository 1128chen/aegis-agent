"""OpenAI-compatible chat endpoint that serves a fine-tuned (LoRA) adapter.

The runtime already talks to any OpenAI-compatible ``/chat/completions`` server
through ``aegis.llm.JsonChatClient``, so this endpoint is the "weights feed
back into the agent" half of the loop: point the ``custom`` provider at
``http://127.0.0.1:<port>/v1`` and set the model id to the served adapter.
"""
import json
import os
from typing import Any, Dict, List, Optional

from .hub import cached_model_path


def resolve_base_model(adapter_dir: str, fallback: str = "") -> str:
    """Read the base model recorded next to the adapter, if present."""
    for filename in ("metrics.json", "adapter_config.json"):
        path = os.path.join(adapter_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        value = data.get("base_model") or (data.get("_name_or_path"))
        if value:
            return str(value)
    return fallback or "Qwen/Qwen2.5-Coder-0.5B-Instruct"


def _render_prompt(messages: List[Dict[str, str]], tokenizer=None) -> str:
    system = "\n".join(
        str(item.get("content", ""))
        for item in messages if item.get("role") == "system"
    )
    user_parts = [
        str(item.get("content", ""))
        for item in messages if item.get("role") in {"user", "developer"}
    ]
    user = "\n".join(user_parts)
    try:
        if tokenizer is not None and getattr(tokenizer, "chat_template", None):
            return tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
    except Exception:
        pass
    text = system.strip()
    if text and user:
        return "%s\n\n%s" % (text, user)
    return user or text


def build_app(adapter_dir: str, base_model: str = "", max_new_tokens: int = 512) -> Any:
    """Build a FastAPI app serving one adapter. Imported lazily (fastapi/uvicorn)."""
    from fastapi import FastAPI, Request

    model_pipe = _load_pipe(adapter_dir, base_model)
    app = FastAPI(title="aegis-flywheel-serve")

    @app.get("/health")
    def health():
        return {"status": "ok", "model": model_pipe["model_id"]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages") or []
        max_tokens = int(body.get("max_tokens") or max_new_tokens)
        temperature = float(body.get("temperature") or 0.0)
        content = _complete(model_pipe, messages, max_tokens, temperature)
        usage = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        return {
            "id": "flywheel-" + model_pipe["model_id"],
            "object": "chat.completion",
            "model": model_pipe["model_id"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": usage,
        }
    return app


def _load_pipe(adapter_dir: str, base_model: str = "") -> Dict[str, Any]:
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "flywheel serving requires torch, transformers and peft "
            "(pip install torch transformers peft safetensors); missing: %s" % exc
        ) from exc
    resolved = resolve_base_model(adapter_dir, base_model)

    def load_offline_fallback(loader, *args, **kwargs):
        # Prefer the local cache snapshot when present so an unreachable HF hub
        # never blocks loading (the runtime keeps working fully offline).
        snapshot = cached_model_path(str(args[0]))
        if snapshot:
            return loader(snapshot, local_files_only=True, **kwargs)
        try:
            return loader(*args, **kwargs)
        except Exception:
            return loader(*args, local_files_only=True, **kwargs)

    tokenizer = load_offline_fallback(
        AutoTokenizer.from_pretrained, resolved, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = load_offline_fallback(
        AutoModelForCausalLM.from_pretrained, resolved,
        trust_remote_code=True, torch_dtype=torch.float32,
    ).to(torch.device("cpu"))
    model = PeftModel.from_pretrained(model, adapter_dir)
    model.eval()
    return {"model": model, "tokenizer": tokenizer, "model_id": "flywheel/" + os.path.basename(adapter_dir)}


def _complete(pipe: Dict[str, Any], messages: List[Dict[str, str]],
              max_tokens: int, temperature: float) -> str:
    import torch

    model = pipe["model"]
    tokenizer = pipe["tokenizer"]
    prompt = _render_prompt(messages, tokenizer)
    inputs = tokenizer(prompt, return_tensors="pt")
    if inputs["input_ids"].shape[1] > 2048:
        inputs = {key: value[:, -2048:] for key, value in inputs.items()}
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max(8, min(int(max_tokens), 2048)),
            do_sample=temperature > 0,
            temperature=temperature if temperature > 0 else 1.0,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    new_ids = output_ids[0, inputs["input_ids"].shape[1]:]
    text = tokenizer.decode(new_ids, skip_special_tokens=True).strip()
    return text


def start(adapter_dir: str, port: int = 8130, host: str = "127.0.0.1",
          base_model: str = "") -> Any:
    """Start uvicorn in-process (blocking). Used by the CLI / scheduler."""
    import uvicorn
    app = build_app(adapter_dir, base_model=base_model)
    uvicorn.run(app, host=host, port=int(port), log_level="warning")
    return app
