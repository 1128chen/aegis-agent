"""Trajectory curation + episode dataset (Hermes-style trajectory-as-training)."""
import json
import os
import tempfile
import unittest

from aegis.flywheel.curation import curate_tasks, task_quality
from aegis.flywheel.datasets import episode_from_traces
from aegis.flywheel.pipeline import validate_sample
from aegis.flywheel.train import label_json, prompt_text
from aegis.models import Finding, ReviewReport, Severity, TaskState, TraceEvent
from aegis.store import TaskStore

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+code = eval(completion.choices[0].message.content)\n"


def _seed(store, task_id="cur-task-1", trace=True, human_accept=False):
    finding = Finding(
        rule_id="AI-EXEC-MODEL-OUTPUT", severity=Severity.CRITICAL,
        title="Model output executed", explanation="LLM text reaches eval",
        path="app.py", line=1, evidence=DIFF.splitlines()[-1],
        fix="parse into a fixed command set", test="injection regression",
        confidence=0.95, source="security", risk_class="llm-misuse",
    )
    store.create(task_id, "demo/ai-app", None, {"source": "test"}, "default")
    store.save_task_payload(task_id, DIFF)
    store.succeed(task_id, ReviewReport(
        repository="demo/ai-app", pull_request=None, summary="s", risk="high",
        findings=[finding],
    ), TraceEvent(1, TaskState.SUCCESS, "done", "2026-09-07T00:00:00+00:00"))
    if trace:
        store.save_llm_trace({
            "task_id": task_id, "role": "security", "step": 1, "event_type": "llm",
            "model": "m", "provider": "p",
            "prompt": {"system": "You are the Security Agent.",
                       "user": '{"diff":"code = eval(...)"}'},
            "reply": {"action": "final", "findings": [
                {"rule_id": "AI-EXEC-MODEL-OUTPUT", "path": "app.py", "line": 1,
                 "severity": "critical", "title": "Model output executed",
                 "explanation": "x", "evidence": "code = eval(completion.choices[0].message.content)",
                 "risk_class": "llm-misuse"},
            ]},
        })
    if human_accept:
        store.save_task_label(
            task_id, "finding_accept", score=1.0, label="accept",
            scored_by="human", note="ok",
        )
    return finding


class CurationTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def test_curation_writes_quality_label(self):
        _seed(self.store, "cur-task-1", trace=True, human_accept=True)
        stats = curate_tasks(self.store)
        self.assertGreaterEqual(stats["scored"], 1)
        quality = task_quality(self.store, "cur-task-1")
        self.assertIsNotNone(quality)
        self.assertGreaterEqual(quality, 0.9)  # accepted + human + traces
        # idempotent: second run marks already, no duplicate score
        again = curate_tasks(self.store)
        self.assertGreaterEqual(again["already"], 1)

    def test_episode_builder_and_min_quality(self):
        _seed(self.store, "cur-task-1", trace=True, human_accept=True)
        curate_tasks(self.store)
        samples = episode_from_traces(self.store)
        self.assertEqual(1, len(samples))
        sample = samples[0]
        self.assertIsNone(validate_sample(sample))
        self.assertEqual("episode", sample["kind"])
        self.assertIn("steps", sample["payload"]["input"])
        self.assertEqual("final", sample["payload"]["response"]["action"])
        # a curated-but-lower task (no human accept => 0.7) is filtered out
        _seed(self.store, "cur-task-low", trace=True, human_accept=False)
        curate_tasks(self.store)
        by_task = {s["task_id"] for s in episode_from_traces(
            self.store, min_quality=0.8
        )}
        self.assertEqual({"cur-task-1"}, by_task)

    def test_no_trace_task_yields_no_episode(self):
        _seed(self.store, "cur-task-2", trace=False)
        self.assertEqual([], episode_from_traces(self.store))

    def test_train_text_for_episode(self):
        _seed(self.store, "cur-task-1", trace=True, human_accept=True)
        sample = episode_from_traces(self.store)[0]
        text = prompt_text(sample)
        self.assertIn("code-review episode", text)
        self.assertIn("You are the Security Agent", text)
        out = label_json(sample)
        self.assertIn('"action": "final"', out)
        parsed = json.loads(out)
        self.assertTrue(parsed["findings"])


if __name__ == "__main__":
    unittest.main()
