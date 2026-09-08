"""Trajectory replay consistency guard (data-integrity regression check)."""
import os
import tempfile
import unittest

from aegis.flywheel.replay import replay_consistency
from aegis.models import Finding, ReviewReport, Severity, TaskState, TraceEvent
from aegis.store import TaskStore

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+code = eval(completion.choices[0].message.content)\n"
DIFF2 = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+data = yaml.load(stream)\n"


def _seed(store, task_id, diff, rule_id, path, trace_matches, source="security"):
    finding = Finding(
        rule_id=rule_id, severity=Severity.HIGH, title="t", explanation="e",
        path=path, line=1, evidence=diff.splitlines()[-1], fix="f", test="t",
        confidence=0.9, source=source, risk_class="prompt-injection",
    )
    store.create(task_id, "demo/ai-app", None, {"source": "test"}, "default")
    store.save_task_payload(task_id, diff)
    store.succeed(task_id, ReviewReport(
        repository="demo/ai-app", pull_request=None, summary="s", risk="high",
        findings=[finding],
    ), TraceEvent(1, TaskState.SUCCESS, "done", "2026-09-07T00:00:00+00:00"))
    if trace_matches:
        store.save_llm_trace({
            "task_id": task_id, "role": "security", "step": 1, "event_type": "llm",
            "model": "m", "provider": "p",
            "prompt": {"system": "s", "user": "u"},
            "reply": {"action": "final", "findings": [
                {"rule_id": rule_id, "path": path, "line": 1,
                 "severity": "high", "title": "t"},
            ]},
        })
    else:
        # trace exists but reports a different finding => data-integrity gap
        store.save_llm_trace({
            "task_id": task_id, "role": "security", "step": 1, "event_type": "llm",
            "model": "m", "provider": "p",
            "prompt": {"system": "s", "user": "u"},
            "reply": {"action": "final", "findings": [
                {"rule_id": "SOMETHING-ELSE", "path": path, "line": 1},
            ]},
        })


class ReplayConsistencyTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def test_consistent_traces_pass(self):
        _seed(self.store, "r1", DIFF, "AI-EXEC-MODEL-OUTPUT", "app.py", True)
        result = replay_consistency(self.store)
        self.assertEqual(1, result["checked"])
        self.assertEqual(1, result["consistent"])
        self.assertEqual(0, result["inconsistent"])
        self.assertTrue(result["guard_passed"])

    def test_missing_worker_finding_is_reported(self):
        _seed(self.store, "r1", DIFF, "AI-EXEC-MODEL-OUTPUT", "app.py", False)
        result = replay_consistency(self.store)
        self.assertEqual(1, result["checked"])
        self.assertEqual(0, result["consistent"])
        self.assertEqual(1, result["inconsistent"])
        self.assertFalse(result["guard_passed"])
        self.assertEqual("AI-EXEC-MODEL-OUTPUT",
                         result["mismatches"][0]["sample"]["rule_id"])

    def test_scanner_sourced_findings_are_not_guarded(self):
        # scanner-sourced findings are not expected in llm_traces
        _seed(self.store, "r1", DIFF2, "AI-SHELL-MODEL-OUTPUT", "x.py", True,
              source="local-rule-scanner")
        result = replay_consistency(self.store)
        self.assertEqual(0, result["checked"])  # nothing worker-sourced to guard
        self.assertTrue(result["guard_passed"])


if __name__ == "__main__":
    unittest.main()
