"""Hermes-style experience mining: completed reviews -> reusable Skill learning."""
import json
import os
import tempfile
import unittest

from aegis import experience
from aegis.config import Settings
from aegis.models import Finding, ReviewReport, Severity, TaskState, TraceEvent
from aegis.service import ReviewService
from aegis.store import TaskStore

DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+code = eval(completion.choices[0].message.content)\n"


def _ai_report(rule_id="AI-EXEC-MODEL-OUTPUT", risk_class="llm-misuse", path="app.py"):
    finding = Finding(
        rule_id=rule_id, severity=Severity.CRITICAL, title="Model output executed",
        explanation="LLM text reaches eval at the sink", path=path, line=1,
        evidence=DIFF.splitlines()[-1], fix="parse into a fixed command set",
        test="injection regression", confidence=0.95, source="security",
        risk_class=risk_class,
    )
    return ReviewReport(
        repository="demo/ai-app", pull_request=None, summary="found ai risk",
        risk="high", findings=[finding],
    )


def _seed_task(store, task_id="task-exp-1", rule_id="AI-EXEC-MODEL-OUTPUT"):
    store.create(task_id, "demo/ai-app", None, {"source": "test"}, "default")
    store.save_task_payload(task_id, DIFF)
    store.succeed(
        task_id, _ai_report(rule_id=rule_id),
        TraceEvent(1, TaskState.SUCCESS, "done", "2026-09-07T00:00:00+00:00"),
    )


class ExperienceStoreTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def test_suggestion_crud_and_dedupe(self):
        sid = self.store.save_experience_suggestion(
            "demo/ai-app", "AI-X", "prompt-injection", "t", "why", {"k": 1},
            task_id="t1",
        )
        self.assertGreater(sid, 0)
        self.assertTrue(self.store.has_experience_suggestion("demo/ai-app", "AI-X"))
        item = self.store.get_experience_suggestion(sid)
        self.assertEqual({"k": 1}, item["payload"])
        self.assertTrue(self.store.set_experience_suggestion_status(sid, "applied", 7))
        rows = self.store.list_experience_suggestions(status="applied")
        self.assertEqual(1, len(rows))
        self.assertEqual(7, rows[0]["artifact_version"])


class ExperienceMiningTests(unittest.TestCase):
    def test_clusters_ai_findings_only(self):
        report = _ai_report().to_dict()
        report["findings"] = report["findings"] + [{
            "rule_id": "SEC-EVAL", "severity": "high", "path": "a.py", "line": 1,
            "title": "plain", "risk_class": None,
        }]
        suggestions = experience.suggestions_from_report("demo/ai-app", report)
        self.assertEqual(1, len(suggestions))
        self.assertEqual("llm-misuse", suggestions[0]["risk_class"])
        self.assertTrue(suggestions[0]["marker"])

    def test_render_and_build_artifact(self):
        suggestion = experience.suggestions_from_report(
            "demo/ai-app", _ai_report().to_dict()
        )[0]
        block = experience.render_learned_block(suggestion)
        self.assertIn("aegis:learned:", block)
        artifact = experience.build_candidate_artifact(suggestion, base_md="# base\n")
        self.assertIsNotNone(artifact)
        self.assertEqual("ai-app-security", artifact["name"])
        self.assertIn("aegis:learned:AI-EXEC-MODEL-OUTPUT:start",
                      artifact["files"]["SKILL.md"])
        # already learned -> no duplicate artifact
        self.assertIsNone(experience.build_candidate_artifact(
            suggestion, base_md=artifact["files"]["SKILL.md"]))

    def test_mine_task_persists_and_deduplicates(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        store = TaskStore(path)
        _seed_task(store)
        first = experience.mine_task(store, "task-exp-1")
        self.assertGreaterEqual(first["mined"], 1)
        second = experience.mine_task(store, "task-exp-1")
        self.assertGreaterEqual(second["skipped"], 1)
        self.assertEqual(1, len(store.list_experience_suggestions(status="pending")))


class ExperienceServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: [__import__("shutil").rmtree(self.tmp, ignore_errors=True)])
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.db)
        env = dict(os.environ)
        env.update({"AEGIS_LLM_PROVIDER": "local", "AEGIS_MEMORY_ENABLED": "false",
                    "AEGIS_AUTH_REQUIRED": "false", "AEGIS_DB_PATH": self.db,
                    "AEGIS_FLYWHEEL_AUTO_TRAIN": "false"})
        for key, value in env.items():
            os.environ[key] = value
        self.service = ReviewService(Settings.from_env())

    def test_after_review_success_mines_and_accept_applies_artifact(self):
        _seed_task(self.service.store, "task-exp-svc", "AI-SHELL-MODEL-OUTPUT")
        self.service._after_review_success("task-exp-svc", "default")
        pending = self.service.list_experience_suggestions("pending")["suggestions"]
        self.assertGreaterEqual(len(pending), 1)
        suggestion = pending[0]
        result = self.service.accept_experience_suggestion(
            suggestion["id"], "default",
        )
        self.assertTrue(result["applied"])
        active = self.service.store.get_active_skill_artifact(
            "ai-app-security", "default"
        )
        self.assertIsNotNone(active)
        md = active["artifact"]["files"]["SKILL.md"]
        self.assertIn("aegis:learned:", md)
        self.assertEqual("applied", self.service.store.get_experience_suggestion(
            suggestion["id"])["status"])
        dismissed = self.service.dismiss_experience_suggestion(
            self.service.list_experience_suggestions("pending")["suggestions"][0]["id"],
            "default",
        ) if self.service.list_experience_suggestions("pending")["suggestions"] else None
        self.assertTrue(dismissed is None or dismissed["dismissed"])


if __name__ == "__main__":
    unittest.main()
