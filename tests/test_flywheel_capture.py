"""Flywheel trajectory capture: store tables and the runtime trace sink."""
import os
import tempfile
import unittest

from aegis import agentic_core
from aegis.llm import JsonChatClient
from aegis.runtime import AgentTool, ToolRegistry
from aegis.store import TaskStore
from aegis.telemetry import ExecutionLedger


class FlywheelStoreTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = TaskStore(self.path)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_save_and_load_llm_trace(self):
        trace_id = self.store.save_llm_trace({
            "task_id": "task-1", "role": "security", "step": 2,
            "event_type": "llm", "model": "fake", "provider": "fake",
            "prompt": {"system": "be safe", "user": "{\"diff\":\"...\"}"},
            "reply": {"action": "final", "findings": []},
        })
        self.assertGreater(trace_id, 0)
        rows = self.store.list_task_llm_traces("task-1")
        self.assertEqual(1, len(rows))
        self.assertEqual("security", rows[0]["role"])
        self.assertEqual("be safe", rows[0]["prompt"]["system"])
        self.assertEqual("final", rows[0]["reply"]["action"])

    def test_tool_trace_round_trip(self):
        self.store.save_llm_trace({
            "task_id": "task-1", "role": "security", "step": 1,
            "event_type": "tool", "tool_name": "read_file", "tool_ok": True,
            "tool_args": {"path": "app.py"}, "result_preview": "content...",
        })
        rows = self.store.list_llm_traces(0, 100)
        self.assertEqual(1, len(rows))
        self.assertEqual("read_file", rows[0]["tool_name"])
        self.assertEqual({"path": "app.py"}, rows[0]["tool_args"])
        self.assertTrue(rows[0]["tool_ok"])
        self.assertEqual("content...", rows[0]["result_preview"])

    def test_labels_crud(self):
        label_id = self.store.save_task_label(
            "task-1", "finding_accept", score=0.9, label="accept",
            scored_by="auto_rule", note="hits ground truth",
            metadata={"rule_id": "AI-EXEC-MODEL-OUTPUT"},
        )
        self.assertGreater(label_id, 0)
        labels = self.store.list_task_labels("task-1")
        self.assertEqual(1, len(labels))
        self.assertEqual("accept", labels[0]["label"])
        self.assertEqual("AI-EXEC-MODEL-OUTPUT", labels[0]["metadata"]["rule_id"])
        by_auto = self.store.list_task_labels_all(scored_by="auto_rule")
        self.assertEqual(1, len(by_auto))
        self.assertEqual([], self.store.list_task_labels_all(scored_by="human"))


class FakeJsonClient(JsonChatClient):
    def __init__(self, responses):
        super().__init__("http://local.invalid", "k", "fake-model", provider="fake")
        self.responses = list(responses)

    def complete_json(self, role, system, user, ledger=None, max_tokens=None):
        action = (
            self.responses.pop(0)
            if self.responses else {"action": "final", "findings": []}
        )
        if ledger is not None:
            ledger.record_model(role, self.provider, self.model, {}, 5, True)
        return action


class TraceSinkTests(unittest.TestCase):
    def test_bounded_role_emits_llm_and_tool_traces(self):
        client = FakeJsonClient([
            {"action": "tool", "tool": "echo", "arguments": {"msg": "hi"},
             "reason": "probe"},
            {"action": "final", "findings": [
                {"rule_id": "X", "path": "a.py", "line": 1, "severity": "high",
                 "title": "t", "explanation": "e", "evidence": "x",
                 "risk_class": "prompt-injection"},
            ]},
        ])
        registry = ToolRegistry([
            AgentTool(
                "echo", "echo a message",
                {"type": "object", "properties": {"msg": {"type": "string"}},
                 "required": ["msg"], "additionalProperties": False},
                lambda msg: {"echo": msg},
            ),
        ])
        records = []
        role = agentic_core.BoundedRole(
            "security", "be safe", client, 8000, 60, max_steps=4,
        )
        role.trace_sink = records.append
        ledger = ExecutionLedger("agentic")
        result = role.run('{"diff": "..."}', registry, ledger)

        llm_events = [item for item in records if item["event_type"] == "llm"]
        tool_events = [item for item in records if item["event_type"] == "tool"]
        self.assertEqual(2, len(llm_events))
        self.assertEqual(1, len(tool_events))
        self.assertEqual("final", result["action"])
        # LLM event carries full prompt content and the public action reply.
        self.assertEqual("be safe", llm_events[0]["prompt"]["system"])
        self.assertIn("diff", llm_events[0]["prompt"]["user"])
        self.assertEqual("tool", llm_events[0]["reply"]["action"])
        self.assertEqual("echo", tool_events[0]["tool_name"])
        self.assertEqual({"msg": "hi"}, tool_events[0]["tool_args"])
        self.assertEqual("fake", llm_events[0]["provider"])
        self.assertEqual("security", llm_events[0]["role"])
        self.assertEqual(1, llm_events[0]["step"])
        self.assertEqual(2, llm_events[1]["step"])

    def test_trace_sink_failure_is_swallowed(self):
        client = FakeJsonClient([{"action": "final", "findings": []}])

        def boom(record):
            raise RuntimeError("store unavailable")

        role = agentic_core.BoundedRole(
            "lead", "plan", client, 8000, 60, max_steps=2,
        )
        role.trace_sink = boom
        ledger = ExecutionLedger("agentic")
        result = role.run("{}", ToolRegistry(), ledger)
        self.assertEqual("final", result["action"])

    def test_agentic_capture_sink_stamps_task_id(self):
        records = []
        reviewer = agentic_core.AgenticReviewer.__new__(agentic_core.AgenticReviewer)
        reviewer.capture_sink = records.append
        sink = reviewer._capture_sink_for("task-abc")
        sink({"event_type": "llm", "role": "lead", "step": 1})
        self.assertEqual("task-abc", records[0]["task_id"])
        reviewer.capture_sink = None
        self.assertIsNone(reviewer._capture_sink_for("task-abc"))


if __name__ == "__main__":
    unittest.main()
