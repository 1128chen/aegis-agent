"""Self-contained offline presentation of the AegisAgent self-optimization loop.

Runs four panels with no model API key:
  1. Vertical AI-application scenario: the ai-app-security cold scanner on a PR.
  2. Corpus + scanner benchmark (precision/recall/F1 per the checked-in corpus).
  3. Flywheel data pipeline: seed -> auto labels -> classify dataset manifest.
  4. Previously trained adapter: registered version, metrics, files.

Usage:  python scripts/present_demo.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aegis.ai_risks import AiApplicationRuleReviewer  # noqa: E402
from aegis.diff_parser import parse_unified_diff  # noqa: E402
from aegis.flywheel.pipeline import load_corpus, run_pipeline  # noqa: E402
from aegis.models import ReviewReport, TaskState, TraceEvent  # noqa: E402
from aegis.store import TaskStore, utc_now  # noqa: E402

SEP = "=" * 72


def panel(title, body):
    print("\n%s\n[ %s ]\n%s\n%s" % (SEP, title, SEP, body))


def scanner_panel():
    scanner = AiApplicationRuleReviewer()
    demo_diff = (
        "--- a/chat.py\n+++ b/chat.py\n@@ -1 +1,2 @@\n-old\n"
        "+system_prompt = SYSTEM_TEMPLATE + request.json['tail']\n"
        "+code = eval(completion.choices[0].message.content)\n"
    )
    parsed = parse_unified_diff(demo_diff)
    findings = scanner.review(demo_diff, parsed)
    lines = ["sample diff:", demo_diff.rstrip(), "cold scanner findings:"]
    for f in findings:
        lines.append("  - %s [%s] %s:%s %s" % (
            f.rule_id, f.risk_class, f.path, f.line, f.title))
    return "\n".join(lines)


def benchmark_panel():
    from scripts.ai_app_benchmark import benchmark
    import json as _json
    report = benchmark("")
    return _json.dumps({
        "corpus_cases": report["cases"],
        "true_positives": report["true_positives"]["total"],
        "false_positives": report["false_positives"]["total"],
        "precision": report["precision"],
        "recall": report["recall"],
        "f1": report["f1"],
        "clean_cases_false_positives": report["clean_cases_fp"],
    }, indent=2)


def pipeline_panel():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = TaskStore(db)
    scanner = AiApplicationRuleReviewer()
    corpus = load_corpus()
    seeded = 0
    for index, case in enumerate(corpus):
        if not case.get("expected"):
            continue
        diff = case["diff"]
        findings = scanner.review(diff, parse_unified_diff(diff))
        if not findings:
            continue
        task_id = "demo-%03d" % index
        store.create(task_id, "demo/ai-app", None, {"source": "demo"}, "default")
        store.save_task_payload(task_id, diff)
        store.succeed(task_id, ReviewReport(
            repository="demo/ai-app", pull_request=None, summary="seeded",
            risk="high", findings=findings,
        ), TraceEvent(1, TaskState.SUCCESS, "seeded", utc_now()))
        seeded += len(findings)

    manifest = run_pipeline(
        store, output_dir=tempfile.mkdtemp(prefix="sft-"),
        options={"classify": True, "instruct": True, "preference": True,
                 "max_tasks": 400},
    )
    os.unlink(db)
    return "\n".join([
        "seeded deterministic findings (runtime->labels source): %d" % seeded,
        "dataset_version: %s" % manifest["dataset_version"][:16] + "…",
        "samples by kind : %s" % json.dumps(manifest["counts"]),
        "risk distribution: %s" % json.dumps(manifest["risk_distribution"]),
        "auto labels written (finding_accept): %s" % json.dumps(
            {k: v for k, v in manifest["labeling"].items() if k != "skipped"}),
        "manifest path   : %s" % manifest["manifest_path"],
    ])


def adapter_panel():
    db_path = "data/demo_flywheel.db"
    if not os.path.isfile(db_path):
        return "(demo db not found — run scripts/run_real_lora.py first)"
    store = TaskStore(db_path)
    active = store.get_active_adapter("preflight")
    rows = [
        ("active preflight adapter", active.get("version") if active else None),
        ("serving model id", active.get("serving_model_id") if active else None),
        ("status", active.get("status") if active else None),
        ("quality score (1/(1+val_loss))", active.get("score") if active else None),
    ]
    runs = store.list_training_runs()
    metrics = {}
    if runs:
        metrics = runs[0].get("metrics") or {}
        rows.insert(0, ("training run id", runs[0]["id"]))
    text = "\n".join("  %-24s %s" % (k, v) for k, v in rows)
    if metrics:
        text += "\n  train loss: %s | val loss: %s | samples: %s | duration_s: %s" % (
            metrics.get("train_loss"), metrics.get("val_loss"),
            metrics.get("samples"), metrics.get("duration_s"))
        if metrics.get("loss_csv"):
            text += "\n  loss curve: %s" % metrics["loss_csv"]
    if active and active.get("adapter_dir") and os.path.isdir(active["adapter_dir"]):
        text += "\n  adapter files: %s" % ", ".join(sorted(os.listdir(active["adapter_dir"]))[:6])
    return text


def main():
    panel("1. Vertical scenario — AI/LLM application security cold scanner",
          scanner_panel())
    panel("2. Corpus + scanner benchmark (scripts/ai_app_benchmark.py)",
          benchmark_panel())
    panel("3. Flywheel data pipeline (traces -> labels -> LoRA-ready dataset)",
          pipeline_panel())
    panel("4. Trained + gated + activated adapter (weight feedback ready)",
          adapter_panel())
    print("\n%s\n演示结束:以上四层可分别对接 CLI / API(见 README)。\n%s" % (SEP, SEP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
