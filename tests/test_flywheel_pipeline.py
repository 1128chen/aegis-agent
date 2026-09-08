"""Flywheel data pipeline: dataset builders, cleaning, idempotent export."""
import json
import os
import shutil
import tempfile
import unittest

from aegis.flywheel.datasets import (
    classify_from_corpus, dataset_version, dedupe_samples,
)
from aegis.flywheel.pipeline import (
    export_pool, load_corpus, run_pipeline, validate_sample,
)
from aegis.models import Finding, ReviewReport, Severity, TaskState, TraceEvent
from aegis.store import TaskStore

DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n"
    "+code = eval(completion.choices[0].message.content)\n"
)


def _seed_store(path: str) -> TaskStore:
    store = TaskStore(path)
    task_id = "task-demo"
    store.create(task_id, "demo/app", None, {"source": "test"}, "default")
    store.save_task_payload(task_id, DIFF)
    finding = Finding(
        rule_id="AI-EXEC-MODEL-OUTPUT", cwe="CWE-95", severity=Severity.CRITICAL,
        title="Model output executed", explanation="LLM text reaches eval",
        path="app.py", line=1, evidence=DIFF.splitlines()[-1],
        fix="parse into a fixed command set", test="injection regression",
        confidence=0.95, source="local-rule-scanner", risk_class="llm-misuse",
    )
    report = ReviewReport(
        repository="demo/app", pull_request=None, summary="found ai risk",
        risk="high", findings=[finding],
    )
    store.succeed(
        task_id, report,
        TraceEvent(1, TaskState.SUCCESS, "done", "2026-09-06T00:00:00+00:00"),
    )
    # A captured worker turn whose final findings were accepted (instruct seed).
    store.save_llm_trace({
        "task_id": task_id, "role": "security", "step": 2, "event_type": "llm",
        "model": "deepseek", "provider": "deepseek",
        "prompt": {"system": "You are the Security Agent for an AI app.",
                   "user": '{"diff":"code = eval(...)","instruction":"review"}',
                   },
        "reply": {"action": "final", "findings": [
            {"rule_id": "AI-EXEC-MODEL-OUTPUT", "path": "app.py", "line": 1,
             "severity": "critical", "title": "Model output executed",
             "explanation": "LLM text reaches eval", "evidence": DIFF.splitlines()[-1],
             "risk_class": "llm-misuse"},
        ]},
    })
    # A human false-positive on a different rule (preference rejected candidate).
    store.record_failure_case(task_id, "false_positive", {
        "finding": {
            "rule_id": "SEC-HARDCODED-SECRET", "path": "app.py", "line": 1,
            "severity": "high", "title": "not a secret", "explanation": "false hit",
            "risk_class": None,
        },
        "note": "this is not a credential",
    })
    return store


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.db_path)
        self.store = _seed_store(self.db_path)

    def test_corpus_loader_and_classify_samples(self):
        corpus = load_corpus()
        self.assertGreaterEqual(len(corpus), 9)
        samples = classify_from_corpus(corpus)
        kinds = set()
        for sample in samples:
            self.assertIsNone(validate_sample(sample))
            response = sample["payload"]["response"]
            if response.get("keep"):
                self.assertIn(response["risk_class"], (
                    "llm-misuse", "prompt-injection", "data-leak-rag"))
            kinds.add(sample["kind"])
        self.assertEqual({"classify"}, kinds)
        positives = [s for s in samples if s["payload"]["response"].get("keep")]
        negatives = [s for s in samples if not s["payload"]["response"].get("keep")]
        self.assertGreaterEqual(len(positives), 4)
        self.assertGreaterEqual(len(negatives), 3)
        # clean corpus case does not leak a positive
        clean_case = next(c for c in corpus if not c["expected"])
        clean_digest = {s["payload"]["response"].get("keep"): s for s in samples}
        self.assertIn(False, [s["payload"]["response"].get("keep")
                              for s in classify_from_corpus([clean_case])])

    def test_full_pipeline_is_idempotent(self):
        first = run_pipeline(
            self.store,
            output_dir=os.path.join(self.tmp, "out"),
            options={"classify": True, "instruct": True, "preference": True},
        )
        self.assertGreaterEqual(first["counts"]["classify"], 1)
        self.assertGreaterEqual(first["counts"]["instruct"], 1)
        self.assertGreaterEqual(first["counts"]["preference"], 1)
        self.assertTrue(os.path.isfile(first["manifest_path"]))
        pool_count = self.store.count_sft_samples(status="selected")
        self.assertGreaterEqual(pool_count, first["total"])

        second = run_pipeline(
            self.store,
            output_dir=os.path.join(self.tmp, "out"),
            options={"classify": True, "instruct": True, "preference": True},
        )
        self.assertEqual(first["dataset_version"], second["dataset_version"])
        self.assertEqual(self.store.count_sft_samples(status="selected"), pool_count)

    def test_label_stats_written(self):
        manifest = run_pipeline(
            self.store,
            output_dir=os.path.join(self.tmp, "out"),
            options={"classify": True},
        )
        self.assertGreaterEqual(manifest["labeling"]["labeled"], 1)
        labels = self.store.list_task_labels("task-demo")
        self.assertGreaterEqual(len(labels), 1)
        self.assertEqual("auto_rule", labels[0]["scored_by"])

    def test_dataset_version_and_dedup(self):
        corpus = load_corpus()
        samples = classify_from_corpus(corpus)
        dup = samples + samples[:3]
        self.assertEqual(len(samples), len(dedupe_samples(dup)))
        version = dataset_version(samples)
        self.assertEqual(64, len(version))
        jsonlines = [json.dumps(s["payload"], sort_keys=True) for s in samples]
        self.assertEqual(jsonlines[0], jsonlines[0])


if __name__ == "__main__":
    unittest.main()
