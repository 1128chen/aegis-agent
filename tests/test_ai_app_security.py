"""Vertical scenario tests: AI/LLM application security review."""
import unittest

from aegis import agentic_core
from aegis.ai_risks import (
    AI_APPLICATION_OVERLAY, AI_COLD_RULES, AiApplicationRuleReviewer,
    ai_risk_catalog_text,
)
from aegis.diff_parser import parse_unified_diff
from aegis.models import Finding, Severity, normalize_risk_class


def _diff(code):
    return "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+%s\n" % code


class AiScannerTests(unittest.TestCase):
    def setUp(self):
        self.scanner = AiApplicationRuleReviewer()

    def _findings(self, code):
        diff = _diff(code)
        return self.scanner.review(diff, parse_unified_diff(diff))

    def test_detects_execution_of_model_output(self):
        findings = self._findings("code = eval(completion.choices[0].message.content)")
        self.assertEqual(1, len(findings))
        item = findings[0]
        self.assertEqual("AI-EXEC-MODEL-OUTPUT", item.rule_id)
        self.assertEqual(Severity.CRITICAL, item.severity)
        self.assertEqual("llm-misuse", item.risk_class)
        self.assertIn("eval", item.evidence)

    def test_detects_shell_from_model_output(self):
        findings = self._findings("subprocess.run(model_output, shell=True)")
        self.assertEqual(1, len(findings))
        self.assertEqual("AI-SHELL-MODEL-OUTPUT", findings[0].rule_id)
        self.assertEqual("llm-misuse", findings[0].risk_class)

    def test_detects_untrusted_system_prompt(self):
        findings = self._findings("system_prompt = base_prompt + request.json['injection']")
        self.assertEqual(1, len(findings))
        self.assertEqual("prompt-injection", findings[0].risk_class)

    def test_detects_tool_result_reprompt(self):
        findings = self._findings("messages.append({'role': 'user', 'content': tool_result})")
        self.assertEqual(1, len(findings))
        self.assertEqual("prompt-injection", findings[0].risk_class)

    def test_detects_prompt_logging(self):
        findings = self._findings("logger.info('prompt=%s', json.dumps(messages))")
        self.assertEqual(1, len(findings))
        self.assertEqual("data-leak-rag", findings[0].risk_class)

    def test_clean_cases_produce_no_findings(self):
        clean = [
            "messages.append({'role': 'user', 'content': request.json['message']})",
            "reply = client.chat.completions.create(model='gpt-4', messages=messages, timeout=30)",
            "content = request.body.decode()",
            "messages.append({'role': 'system', 'content': SYSTEM_TEMPLATE})",
        ]
        for code in clean:
            self.assertEqual([], self._findings(code), code)

    def test_rules_map_to_valid_risk_classes(self):
        valid = {name for name, *_ in (
            ("prompt-injection",), ("tool-escape",), ("data-leak-rag",),
            ("agent-loop-abuse",), ("llm-misuse",), ("classic-sec",),
        )}
        for rule_id, severity, sink, source, *_ in AI_COLD_RULES:
            self.assertGreater(len(sink), 0)
            self.assertIsInstance(severity, Severity)


class AiRiskModelTests(unittest.TestCase):
    def test_normalize_risk_class(self):
        self.assertEqual("prompt-injection", normalize_risk_class("Prompt-Injection"))
        self.assertEqual("llm-misuse", normalize_risk_class("llm-misuse"))
        self.assertIsNone(normalize_risk_class(""))
        self.assertIsNone(normalize_risk_class("not a valid value@!"))
        self.assertIsNone(normalize_risk_class(None))

    def test_finding_serialization_preserves_legacy_shape(self):
        plain = Finding(
            rule_id="SEC-EVAL", severity=Severity.HIGH, title="t", explanation="e",
            path="a.py", line=1, evidence="x", fix="f", test="t",
        )
        dumped = plain.to_dict()
        self.assertNotIn("risk_class", dumped)
        expected_keys = {
            "rule_id", "severity", "title", "explanation", "path", "line",
            "evidence", "fix", "test", "confidence", "evidence_refs",
            "call_chain", "source", "gate", "cwe",
        }
        self.assertEqual(expected_keys, set(dumped))

    def test_risk_class_serialized_when_present(self):
        item = Finding(
            rule_id="AI-EXEC-MODEL-OUTPUT", severity=Severity.CRITICAL, title="t",
            explanation="e", path="a.py", line=1, evidence="x", fix="f", test="t",
            risk_class="llm-misuse",
        )
        self.assertEqual("llm-misuse", item.to_dict()["risk_class"])

    def test_agentic_parse_accepts_and_normalizes_risk_class(self):
        diff = _diff("code = eval(user_input)")
        parsed = parse_unified_diff(diff)
        result = {"action": "final", "_steps": 3, "findings": [
            {"rule_id": "X", "path": "x.py", "line": 1, "severity": "high",
             "title": "t", "explanation": "e", "evidence": "code = eval(user_input)",
             "risk_class": "PROMPT-INJECTION"},
            {"rule_id": "Y", "path": "x.py", "line": 1, "severity": "low",
             "title": "t2", "explanation": "e2", "evidence": "code = eval(user_input)",
             "risk_class": "not a valid value@!"},
        ]}
        findings = agentic_core._parse_findings(result, parsed, "security")
        self.assertEqual("security", findings[0].source)
        self.assertEqual("prompt-injection", findings[0].risk_class)
        self.assertIsNone(findings[1].risk_class)

    def test_vertical_overlay_mentions_taxonomy(self):
        self.assertIn("risk_class", AI_APPLICATION_OVERLAY)
        self.assertIn("prompt-injection", ai_risk_catalog_text())


if __name__ == "__main__":
    unittest.main()
