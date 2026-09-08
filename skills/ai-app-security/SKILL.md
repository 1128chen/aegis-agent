---
name: ai-app-security
description: Review LLM/AI application code changes for prompt injection and boundary confusion, agent tool/capability escape, RAG and conversation data leaks, unbounded or attacker-controlled agent loops, and unsafe handling of model output. Use when a change touches model prompts, agent tools, retrieval, chat history, or model output handling.
allowed-tools:
  - search_diff
  - search_repository
  - read_file
  - changed_line
  - symbol
  - ast_analyze
  - git_context
  - run_scanners
  - run_repository_checks
  - locate_tests
---

# AI / LLM application security review

## Mission

Identify exploitable security defects in an LLM/AI application change. Model the application's trust
boundary as: repository code (trusted) ↔ model call site → model (semi-trusted, steerable) ↔ tools and
retrieved data (untrusted) ↔ developer/system instructions (privileged). A defect is reportable when an
attacker can cross one of these boundaries to steer privileged instructions, execute code, exfiltrate
data, or exhaust resources. A suspicious API alone is a lead, not a finding.

## Review procedure

1. Map every model call site in the change (completion/chat/embedding calls). Record who builds the
   `system` vs `user`/`developer` messages, which data is untrusted, and which tool schemas are exposed.
2. Trace untrusted input (HTTP/request body, webhook, user message, file/URL content, retrieved
   documents, tool results) to prompt text. Distinguish the normal case — user text in a `user`-role
   message — from injection, which requires the untrusted data to steer `system`/instruction text,
   tool definitions/function schemas, output format, or privileged agent decisions.
3. For each agent tool: check least-privilege (permission set, path/scope), whether tool output is
   relabelled as untrusted when fed back to the model, and whether a model-controlled tool can reach a
   shell, filesystem write, secret read, or network exfiltration path.
4. Check data handling: PII/secrets entering context via RAG or prompts; prompt/history/secret values
   written to logs, telemetry, embeddings, or stored outputs without redaction; retention limits.
5. Check the agent loop: bounded steps/retries/time budget, externally controlled termination,
   unbounded memory/context growth, concurrent writes without locks.
6. Check model output handling: eval/subprocess/shell/sql/html from model text, non-schema JSON
   crashes, missing timeouts/fallbacks on model calls, and tool re-invocation driven by model output.
7. Use repository evidence to establish a concrete source-to-sink path before reporting; treat LLM
   safety as an engineering property of the code path, not of the prompt alone.

## High-value checklist

- Untrusted data reaches `system`/`developer` role, `instructions=`, tool `description`, or function
  schema text; separator/history boundary can be confused by crafted content.
- Tool results or retrieved documents appended back into the conversation without being tagged as
  untrusted data (agent can be steered through its own tool input).
- Tool with shell execution / arbitrary command / secret read is reachable by a model or external
  input; exfiltration path memory → model → network/API key or file.
- RAG retrieval returns PII/secrets; prompts, chat history, API keys, or raw model output logged or
  persisted without redaction.
- Agent loop has no step/retry/time budget, or termination depends on untrusted content.
- Model output reaches `eval`/`exec`/`compile`, shell/subprocess with `shell=True`, or an unparameterized
  query; model call lacks timeout/fallback; output assumed to be valid JSON without a schema guard.

## Evidence and severity

Every finding must cite changed-line evidence plus a concrete source-to-sink path, tool/retrieval
observation, or call chain. High severity: prompt-injection that steers privileged instructions,
tool escape with code execution or secret exfiltration, or unbounded resource consumption from
external input. Medium: injection or leakage constrained to a single tenant or low-privilege path.
Low: defense-in-depth gap (missing timeout/fallback) with no demonstrated exploit. Never report
"LLM can be tricked" without a reachable code path in the change.

## Remediation and verification

Prefer fixed system templates with untrusted data isolated in user/content fields; tag tool output and
retrieved data as untrusted; least-privilege tool permissions and explicit allowlists for any
model-selected command; redaction and retention policies for prompt/log/data stores; bounded agent
loops; structured output schemas with strict validation; and parameterised, non-executing sinks for
model output. Require a regression test with a malicious payload and an assertion that the boundary
holds (e.g. injection string never reaches system text; tool never executes the injected command).

## Output contract

Return `title`, `severity`, exact changed lines, `risk_class` from {prompt-injection, tool-escape,
data-leak-rag, agent-loop-abuse, llm-misuse, classic-sec}, `source`/`sink`, `boundary_crossed`,
`untrusted_source`, `exploit_payload_or_sequence`, `impact`, and `recommended_fix`. Omit `risk_class`
when a defect is not AI-specific. If clean, summarise the model call sites and trust boundaries
checked.
