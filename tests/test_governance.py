"""Periodic governance scan + scheduled aggregation (Hermes-style audits)."""
import os
import tempfile
import unittest

from aegis.flywheel.scheduler import FlywheelScheduler
from aegis.governance import persist_report, run_repo_scan
from aegis.models import Finding, ReviewReport, Severity, TaskState, TraceEvent
from aegis.repository_tools import CONFIG_NAMES
from aegis.store import TaskStore

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+code = eval(completion.choices[0].message.content)\n"


def _seed(store, task_id, rule="AI-EXEC-MODEL-OUTPUT", severity="high"):
    finding = Finding(
        rule_id=rule, severity=Severity(severity), title="t", explanation="e",
        path="app.py", line=1, evidence="x", fix="f", test="t", confidence=0.9,
        source="security", risk_class="llm-misuse" if rule.startswith("AI-") else None,
    )
    store.create(task_id, "demo/ai-app", None, {"source": "t"}, "default")
    store.save_task_payload(task_id, DIFF)
    store.succeed(task_id, ReviewReport(
        repository="demo/ai-app", pull_request=None, summary="s", risk=severity,
        findings=[finding],
    ), TraceEvent(1, TaskState.SUCCESS, "done", "2026-09-08T00:00:00+00:00"))


class GovernanceScanTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def test_scan_aggregates_and_persists(self):
        _seed(self.store, "g1", "AI-EXEC-MODEL-OUTPUT", "high")
        _seed(self.store, "g2", "AI-EXEC-MODEL-OUTPUT", "critical")
        _seed(self.store, "g3", "SEC-YAML-LOAD", "high")
        self.store.record_failure_case("g3", "false_positive", {
            "finding": {"rule_id": "SEC-YAML-LOAD"},
        })
        report = run_repo_scan(self.store, "default", "demo/ai-app")
        self.assertEqual(3, report["prs"])
        self.assertEqual(3, report["findings"])
        self.assertEqual(3, report["high_critical"])
        self.assertEqual(1, report["false_positive_feedback"])
        self.assertIn("AI-EXEC-MODEL-OUTPUT",
                      [r["rule_id"] for r in report["top_rules"]])
        report_id = persist_report(self.store, report)
        listing = self.store.list_governance_reports("demo/ai-app")
        self.assertEqual(1, len(listing))
        self.assertEqual(report_id, listing[0]["id"])
        self.assertEqual(3, listing[0]["report"]["prs"])

    def test_scheduler_runs_governance_each_tick(self):
        _seed(self.store, "g1", "AI-EXEC-MODEL-OUTPUT", "high")

        class SettingsStub:
            continuous_eval_seconds = 10
            flywheel_auto_train = False
            flywheel_min_new_labels = 20
            governance_repositories = "demo/ai-app"

        scanned = []

        def runner(repository, push=True):
            scanned.append(repository)
            return run_repo_scan(self.store, "default", repository)

        scheduler = FlywheelScheduler(
            self.store, SettingsStub(), None, None, governance_runner=runner,
        )
        result = scheduler.tick()
        self.assertIn("governance", result)
        self.assertEqual(["demo/ai-app"], scanned)
        # low label delta still returns governance scan
        self.assertFalse(result["ran"])
        self.assertIn("report_id", result["governance"]["demo/ai-app"])

    def test_project_context_file_is_discoverable(self):
        self.assertIn("EVO_REVIEW.md", CONFIG_NAMES)


if __name__ == "__main__":
    unittest.main()
