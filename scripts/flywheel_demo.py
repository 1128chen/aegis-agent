"""End-to-end flywheel demo: seed -> label -> pipeline -> (train) -> gate.

Runs the whole self-optimization data loop against a disposable task store
seeded from the AI application security corpus. Training is optional and needs
``torch + transformers + peft`` installed; without it the script prints the
commands to run training and serve as separate steps.

Example:
    python scripts/flywheel_demo.py
    python scripts/flywheel_demo.py --train   # when the training stack is present
"""
import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aegis.ai_risks import AiApplicationRuleReviewer  # noqa: E402
from aegis.diff_parser import parse_unified_diff  # noqa: E402
from aegis.flywheel.pipeline import load_corpus, run_pipeline  # noqa: E402
from aegis.flywheel.registry import adapter_gate  # noqa: E402
from aegis.models import ReviewReport, TraceEvent  # noqa: E402
from aegis.models import TaskState  # noqa: E402
from aegis.store import TaskStore, utc_now  # noqa: E402


def seed_store(path, corpus):
    store = TaskStore(path)
    scanner = AiApplicationRuleReviewer()
    seeded = 0
    for index, case in enumerate(corpus):
        if not case.get("expected"):
            continue
        diff = case["diff"]
        parsed = parse_unified_diff(diff)
        findings = scanner.review(diff, parsed)
        if not findings:
            continue
        task_id = "demo-%03d-%s" % (index, case.get("name", "case"))
        store.create(task_id, "demo/ai-app", None, {"source": "demo-corpus"},
                     "default")
        store.save_task_payload(task_id, diff)
        report = ReviewReport(
            repository="demo/ai-app", pull_request=None,
            summary="cold-path scanner seeded finding", risk="high",
            findings=findings,
        )
        store.succeed(task_id, report, TraceEvent(
            1, TaskState.SUCCESS, "seeded", utc_now(),
        ))
        seeded += len(findings)
    return store, seeded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="", help="path to ai corpus jsonl")
    parser.add_argument("--db", default="", help="keep the task db at this path")
    parser.add_argument("--out", default="", help="dataset output dir")
    parser.add_argument("--train", action="store_true",
                        help="attempt a real local LoRA training run")
    args = parser.parse_args(argv)

    corpus = load_corpus(args.corpus)
    print("corpus cases: %d" % len(corpus))
    keep_db = args.db or tempfile.mktemp(suffix=".db")
    store, seeded = seed_store(keep_db, corpus)
    print("seeded deterministic findings: %d" % seeded)

    print("\n-- data pipeline --")
    manifest = run_pipeline(
        store,
        output_dir=args.out or os.path.join("data", "sft"),
        options={"classify": True, "instruct": True, "preference": True,
                 "max_tasks": 400},
    )
    print("dataset_version: %s" % manifest["dataset_version"])
    print("total samples:   %d" % manifest["total"])
    print("by kind:         %s" % manifest["counts"])
    print("risk distribution: %s" % manifest["risk_distribution"])
    print("auto labels written: %s" % manifest["labeling"])
    if not args.train:
        print("\nDB: %s" % keep_db)
        print("Next steps (requires torch + peft installed):")
        print("  python -m aegis.flywheel train --layer preflight --kind classify")
        print("  python -m aegis.flywheel serve --adapter-dir artifacts/adapters/<run_id>")
        return 0

    print("\n-- training --")
    try:
        samples = store.list_sft_samples(status="selected", limit=1200)
        result = registry_run(store, samples)
    except RuntimeError as exc:
        print("training unavailable: %s" % exc)
        print("install with: pip install torch transformers peft safetensors accelerate")
        return 2
    run_id = result["run_id"]
    print("run_id:        %s" % run_id)
    print("adapter dir:   %s" % result["adapter_dir"])
    print("metrics:       %s" % result["metrics"])
    gate = adapter_gate(store, result["layer"], result["adapter_version"])
    print("activation gate: %s" % gate)
    print("\nFeed the adapter back to the agent:")
    print("  python -m aegis.flywheel serve --adapter-dir %s" % result["adapter_dir"])
    print("  set AEGIS_MODEL_DEPLOYMENT=distill-role and AEGIS_FLYWHEEL_SERVE_URL")
    return 0


def registry_run(store, samples):
    from aegis.flywheel import registry as flywheel_registry
    from aegis.flywheel.train import default_hyperparams
    hyper = default_hyperparams()
    hyper["epochs"] = 2
    hyper["max_train_examples"] = 300
    return flywheel_registry.run_training_job(
        store, samples, hyper=hyper, layer="preflight", activate=False,
        base_model="Qwen/Qwen2.5-Coder-0.5B-Instruct",
        out_root=os.path.join("artifacts", "adapters"),
    )


if __name__ == "__main__":
    sys.exit(main())
