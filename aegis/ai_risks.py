"""AI/LLM application security vertical: risk taxonomy and cold-path scanner.

This module gives AegisAgent a specialised review scenario for code changes to
LLM/AI applications.  The deterministic rules below are deliberately narrow
cold-path detectors whose evidence lives on a single added line; the deep
reasoning about reachability lives in the ``ai-app-security`` Agent Skill that
the model roles consume at runtime.
"""
import hashlib
import re
from typing import List, Optional, Tuple

from .models import Finding, Severity
from .reviewer import Reviewer

# Vertical risk taxonomy surfaced on Findings as ``risk_class``.
AI_RISK_CLASSES: Tuple[Tuple[str, str, str], ...] = (
    (
        "prompt-injection",
        "Prompt injection / boundary confusion",
        "Untrusted data controls system or instruction text, tool definitions, or agent "
        "decisions. A user-controlled value in an ordinary user-role message is expected "
        "behaviour; injection means an attacker steers privileged instructions.",
    ),
    (
        "tool-escape",
        "Agent tool / capability boundary",
        "Over-broad tool permissions, dangerous shell execution, or a path that lets a model "
        "or tool exfiltrate secrets or perform privileged actions outside its scope.",
    ),
    (
        "data-leak-rag",
        "Data leak in retrieval, context or logging",
        "PII/secrets enter model context through RAG or prompts and leak into logs, telemetry, "
        "embeddings or stored outputs without redaction.",
    ),
    (
        "agent-loop-abuse",
        "Unbounded or attacker-controlled agent loop",
        "Missing step/retry/time budgets, loops whose termination is externally controlled, or "
        "unbounded resource consumption in an agentic loop.",
    ),
    (
        "llm-misuse",
        "Unsafe handling of model output",
        "Model output is executed (eval/subprocess/shell), parsed unsafely, or used to build "
        "queries without neutralisation; missing timeouts or fallbacks on model calls.",
    ),
    (
        "classic-sec",
        "Classic security baseline in an AI application",
        "Standard code-security defects (secrets, injection, traversal) viewed through the "
        "lens of an AI application's trust boundaries.",
    ),
)

AI_RISK_BY_CODE = {item[0]: item for item in AI_RISK_CLASSES}

# Each deterministic rule maps to one taxonomy class.
_RULE_CLASS = {
    "AI-EXEC-MODEL-OUTPUT": "llm-misuse",
    "AI-SHELL-MODEL-OUTPUT": "llm-misuse",
    "AI-SYSTEM-FROM-UNTRUSTED": "prompt-injection",
    "AI-TOOL-RESULT-REPROMPT": "prompt-injection",
    "AI-PROMPT-TO-LOG": "data-leak-rag",
}

def ai_risk_catalog_text() -> str:
    lines = ["Valid risk_class values:"]
    for code, label, definition in AI_RISK_CLASSES:
        lines.append("- %s (%s): %s" % (code, label, definition))
    return "\n".join(lines)


AI_APPLICATION_OVERLAY = (
    "\nApplication scenario overlay: this change is to an LLM/AI application. "
    "Assign each candidate finding one risk_class from {prompt-injection, tool-escape, "
    "data-leak-rag, agent-loop-abuse, llm-misuse, classic-sec} and include it as an "
    "optional \"risk_class\" field inside each final finding JSON object; omit the field "
    "when a defect is not AI-specific.\n"
    "AI evidence rules:\n"
    "- A user-controlled value flowing into a normal user-role message is expected behaviour "
    "for a chat app, not injection. Report prompt-injection only when untrusted data reaches "
    "the system/instruction text, tool definitions, function schemas, or privileged agent "
    "decisions, or when separator/history boundaries can be confused.\n"
    "- Tool output and retrieved documents are untrusted input to the model, never trusted "
    "instructions.\n"
    "- Check whether model output can reach eval/subprocess/shell or parameterized-query "
    "boundaries, whether tool permission sets are least-privilege, whether conversation or "
    "prompt text is logged or embedded without redaction, and whether agent loops have "
    "bounded steps, retries and time budgets."
)


# (rule_id, severity, sink_pattern, source_pattern, title, explanation, fix, test)
# Both sink and source must match the same added line; source may be empty.
AI_COLD_RULES = (
    (
        "AI-EXEC-MODEL-OUTPUT", Severity.CRITICAL,
        r"(?:^|[^.\w])(eval|exec|compile)\s*\(",
        r"(?:choices\s*\[|\.content\b|model_output|model_response|completion|generated_text|"
        r"reply_text|output_text|response\.text|client\.chat|llm_result)",
        "Model output passed to dynamic code execution",
        "Text produced by an LLM is evaluated or compiled as code. Because model output is "
        "attacker-influenceable through prompts or documents, this can yield code execution.",
        "Do not execute model output. Constrain the model to a fixed output schema, then parse "
        "it into a whitelisted action/command set that is executed without eval.",
        "Add a test where a prompt-injection payload makes the model emit executable code and "
        "assert the application rejects it and performs no execution.",
    ),
    (
        "AI-SHELL-MODEL-OUTPUT", Severity.HIGH,
        r"(?:^|[^.\w])(os\.system|subprocess\.(?:run|Popen|call)|"
        r"asyncio\.create_subprocess_shell|shell\s*=\s*True)\b",
        r"(?:model_output|model_response|completion|generated_text|reply_text|output_text|"
        r"response\.text|choices\s*\[|\.content\b|llm_result)",
        "Model output flows into a shell or subprocess boundary",
        "LLM output reaches a shell/subprocess call. Attacker-controlled model output can then "
        "inject commands, especially with shell=True or string concatenation.",
        "Keep shell=False and pass an argument list, or map model output to a fixed command "
        "whitelist; never interpolate raw text into a shell command.",
        "Add a test with an injected command payload and assert the sink executes only the "
        "intended fixed command.",
    ),
    (
        "AI-SYSTEM-FROM-UNTRUSTED", Severity.HIGH,
        r"(?:system_prompt\s*=|['\"]role['\"]\s*:\s*['\"]system['\"]|"
        r"role\s*=\s*['\"]system['\"]|system_content\s*=|instructions\s*=|"
        r"developer_message\s*=)",
        r"(?:request\.|request\b|body\b|params\b|payload\b|query_string|user_input|incoming|"
        r"raw_|event\b|webhook\b|data\.get|authorization|header\b)",
        "Untrusted input concatenated into the system prompt or instructions",
        "The system/instruction text of a model call is built from request data. System text "
        "carries privileged instructions, so an attacker who reaches it can override the "
        "application's behaviour.",
        "Keep system instructions as a fixed template; only allow untrusted data into user-role "
        "content after a clear boundary. Validate and neutralise any dynamic segment.",
        "Add a test where an injection string is embedded in the request field and assert it "
        "never appears inside the system message.",
    ),
    (
        "AI-TOOL-RESULT-REPROMPT", Severity.HIGH,
        r"(?:messages\.(?:append|insert|extend)|history\.(?:append|extend)|['\"]content['\"]\s*:)",
        r"(?:tool_result|observation|action_output|agent_output|last_result|step_output|"
        r"output\b)",
        "Agent tool result echoed into a subsequent model turn unguarded",
        "A tool result (which may contain data from untrusted sources) is appended straight "
        "back into the conversation. An attacker who controls the tool input can steer the "
        "next model decision by injecting pseudo-instructions.",
        "Tag tool results as untrusted data in the message schema, scope tool instructions to "
        "the system turn, and route raw tool output into a data field the model treats as "
        "facts, not instructions.",
        "Add a test where tool output contains an instruction-like payload and assert the "
        "agent does not treat it as a command.",
    ),
    (
        "AI-PROMPT-TO-LOG", Severity.MEDIUM,
        r"(?:logger|logging|log)\.(?:debug|info|warning|error|exception)\s*\(|print\s*\(",
        r"(?:prompt|messages|conversation|history|completion|api_key|secret|token\b)",
        "Prompt or sensitive context written to logs",
        "A log/print statement emits raw prompts, conversation history or credentials. "
        "Prompts commonly embed PII, secrets or proprietary instructions, so this leaks them "
        "into log stores and bug reports.",
        "Log only anonymised metadata (token counts, latency, outcome); apply a structured "
        "redaction policy before anything user- or model-derived reaches the logs.",
        "Add a test that exercises the logging path and asserts no prompt content or secret "
        "value appears in the emitted log line.",
    ),
)


class AiApplicationRuleReviewer(Reviewer):
    """Deterministic cold-path scanner for AI/LLM application code changes."""

    name = "ai-app-security-agent"
    domains = ("ai-security", "llm", "prompt-injection", "data-leak")

    def review(self, diff: str, parsed) -> List[Finding]:
        findings: List[Finding] = []
        seen = set()
        for line in parsed.added_lines:
            if line.path.endswith((".lock", ".min.js", ".map")):
                continue
            content = line.content
            for (rule_id, severity, sink, source, title,
                 explanation, fix, test) in AI_COLD_RULES:
                sink_match = re.search(sink, content)
                if not sink_match or (source and not re.search(source, content)):
                    continue
                identity = (rule_id, line.path, line.line)
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append(Finding(
                    rule_id=rule_id,
                    severity=severity,
                    title=title,
                    explanation=explanation,
                    path=line.path,
                    line=line.line,
                    evidence=content.strip()[:240],
                    fix=fix,
                    test=test,
                    confidence=0.9,
                    evidence_refs=[{
                        "evidence_id": "ai-rule:%s" % hashlib.sha256(
                            (rule_id + line.path + str(line.line) + content).encode("utf-8")
                        ).hexdigest()[:16],
                        "tool": "ai-app-security-scanner",
                        "rule_id": rule_id,
                        "path": line.path,
                        "line": line.line,
                    }],
                    source="local-rule-scanner",
                    risk_class=_RULE_CLASS.get(rule_id),
                ))
        return findings
