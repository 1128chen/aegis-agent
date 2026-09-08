"""Cross-session review memory: FTS search + repository digest + recall inject."""
import os
import tempfile
import unittest

from aegis.history import DigestAugmentedMemory, repository_digest
from aegis.memory import MemoryManager
from aegis.models import Finding, ReviewReport, Severity, TaskState, TraceEvent
from aegis.store import TaskStore

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+code = eval(completion.choices[0].message.content)\n"


def _seed_report(store, task_id, rules=("AI-EXEC-MODEL-OUTPUT",)):
    store.create(task_id, "demo/ai-app", None, {"source": "t"}, "default")
    store.save_task_payload(task_id, DIFF)
    findings = [Finding(
        rule_id=rule, severity=Severity.HIGH, title="t", explanation="e",
        path="app.py", line=1, evidence="code = eval(completion.choices[0].message.content)",
        fix="f", test="t", confidence=0.9, source="security",
        risk_class="llm-misuse" if rule.startswith("AI-") else None,
    ) for rule in rules]
    store.succeed(task_id, ReviewReport(
        repository="demo/ai-app", pull_request=None, summary="summary with eval risk",
        risk="high", findings=findings,
    ), TraceEvent(1, TaskState.SUCCESS, "done", "2026-09-08T00:00:00+00:00"))


class FtsSearchTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def test_index_and_search_round_trip(self):
        ok = self.store.index_review_text(
            "default", "demo/ai-app", "t-1",
            "AI-EXEC-MODEL-OUTPUT eval model output executed",
        )
        self.assertTrue(ok)
        hits = self.store.search_review_history("default", "demo/ai-app", "eval")
        self.assertEqual(1, len(hits))
        self.assertEqual("t-1", hits[0]["task_id"])
        # other repo / query miss
        self.assertEqual(
            [], self.store.search_review_history("default", "demo/ai-app", "yaml"))
        self.assertEqual(
            [], self.store.search_review_history("default", "other/repo", "eval"))


class DigestTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def test_repository_digest_aggregates(self):
        _seed_report(self.store, "t1", ("AI-EXEC-MODEL-OUTPUT",))
        _seed_report(self.store, "t2", ("AI-EXEC-MODEL-OUTPUT", "SEC-EVAL"))
        self.store.record_failure_case("t2", "false_positive", {
            "finding": {"rule_id": "SEC-EVAL", "path": "a.py", "line": 1},
        })
        digest = repository_digest(self.store, "default", "demo/ai-app")
        self.assertEqual(2, digest["reviewed_prs"])
        rules = {item["rule_id"]: item["count"] for item in digest["top_rules"]}
        self.assertEqual(2, rules["AI-EXEC-MODEL-OUTPUT"])
        self.assertIn("SEC-EVAL", digest["false_positive_rule_ids"])
        self.assertIn("llm-misuse", [r["risk_class"] for r in digest["ai_risks"]])
        self.assertIn("Most common rules", digest["text"])

    def test_augmented_recall_injects_digest(self):
        _seed_report(self.store, "t1", ("AI-EXEC-MODEL-OUTPUT",))
        base = MemoryManager(self.store, enabled=True, recall_limit=3,
                             working_ttl_seconds=600)
        wrapped = DigestAugmentedMemory(base, self.store)
        items = wrapped.recall("default", "demo/ai-app", "eval risk")
        kinds = [item.get("kind") for item in items]
        self.assertIn("repository_digest", kinds)
        digest_item = next(item for item in items if item.get("kind") == "repository_digest")
        self.assertIn("AI-EXEC-MODEL-OUTPUT", digest_item["content"])


if __name__ == "__main__":
    unittest.main()
