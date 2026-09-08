"""Real local-CPU LoRA training over the flywheel classify dataset.

Steps: seed a task store from the AI-app corpus -> run the data pipeline ->
train a Qwen2.5-Coder LoRA adapter on the classify samples -> register run +
adapter version. No LLM API key is needed; model weights come from HuggingFace.

Usage:
    python scripts/run_real_lora.py [--db data/demo_flywheel.db]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aegis.ai_risks import AiApplicationRuleReviewer  # noqa: E402
from aegis.diff_parser import parse_unified_diff  # noqa: E402
from aegis.flywheel.pipeline import load_corpus, run_pipeline  # noqa: E402
from aegis.flywheel.registry import run_training_job  # noqa: E402
from aegis.flywheel.train import default_hyperparams  # noqa: E402
from aegis.models import ReviewReport, TaskState, TraceEvent  # noqa: E402
from aegis.store import TaskStore, utc_now  # noqa: E402


def seed(store, corpus):
    scanner = AiApplicationRuleReviewer()
    count = 0
    for index, case in enumerate(corpus):
        if not case.get("expected"):
            continue
        diff = case["diff"]
        findings = scanner.review(diff, parse_unified_diff(diff))
        if not findings:
            continue
        task_id = "lora-seed-%03d-%s" % (index, case.get("name", "case"))
        store.create(task_id, "demo/ai-app", None, {"source": "corpus"}, "default")
        store.save_task_payload(task_id, diff)
        store.succeed(task_id, ReviewReport(
            repository="demo/ai-app", pull_request=None,
            summary="seeded", risk="high", findings=findings,
        ), TraceEvent(1, TaskState.SUCCESS, "seeded", utc_now()))
        count += len(findings)
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/demo_flywheel.db")
    parser.add_argument("--out", default="artifacts/adapters")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-0.5B-Instruct")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--max-examples", type=int, default=400)
    parser.add_argument("--max-seq", type=int, default=256)
    args = parser.parse_args(argv)

    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    if os.path.exists(args.db):
        os.unlink(args.db)
    store = TaskStore(args.db)
    corpus = load_corpus()
    seeded = seed(store, corpus)
    print("seeded findings:", seeded)

    manifest = run_pipeline(
        store, output_dir=os.path.join("data", "sft"),
        options={"classify": True, "instruct": True, "preference": True,
                 "max_tasks": 400},
    )
    print("pipeline dataset:", json.dumps({
        "dataset_version": manifest["dataset_version"],
        "counts": manifest["counts"],
        "risk": manifest["risk_distribution"],
    }))
    samples = store.list_sft_samples(status="selected", limit=5000)

    hyper = default_hyperparams()
    hyper["epochs"] = max(1, args.epochs)
    hyper["max_train_examples"] = max(1, args.max_examples)
    hyper["max_seq_len"] = max(64, args.max_seq)
    hyper["batch_size"] = 2
    hyper["grad_accum"] = 8
    hyper["val_ratio"] = 0.1
    result = run_training_job(
        store, samples, hyper=hyper, layer="preflight",
        base_model=args.base_model, out_root=args.out,
        activate=False,
    )
    print("TRAIN_RESULT " + json.dumps({
        "run_id": result["run_id"],
        "adapter_version": result["adapter_version"],
        "adapter_dir": result["adapter_dir"],
        "metrics": result["metrics"],
    }, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
