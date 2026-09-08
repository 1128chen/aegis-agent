"""Flywheel dataset builders: turn runtime traces and corpus cases into SFT samples.

Sample kinds
------------
- ``classify``   (input: one added code line -> {"keep", "risk_class", "severity"})
                 Used to fine-tune a cheap preflight/severity role. Labels come from
                 the deterministic AI cold scanner and the labelled evaluation corpus,
                 so this dataset is clean and can be produced without any LLM traffic.
- ``instruct``   (prompt system+user -> final finding JSON) distilled from real
                 accepted worker turns in the trajectory store.
- ``preference`` (chosen accepted finding vs rejected false-positive) for negative
                 sampling / DPO, sourced from human feedback.
"""
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from ..ai_risks import AI_RISK_BY_CODE
from ..diff_parser import parse_unified_diff
from .curation import task_quality

SAMPLE_KINDS = ("classify", "instruct", "preference", "episode")

PREFERENCE_INSTRUCTION = (
    "Below are two review findings for the same code change. Output the better "
    "finding. Prefer evidence that cites the exact changed line and a concrete "
    "risk; reject speculation, wrong severity and vague explanations."
)

MAX_CODE_LEN = 400
MAX_INSTRUCTION_LEN = 24000


def sample_digest(sample: Dict[str, Any]) -> str:
    """Content fingerprint for a sample (dedup / dataset versioning)."""
    payload = sample.get("payload")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataset_version(samples: Iterable[Dict[str, Any]]) -> str:
    digests = sorted(sample_digest(item) for item in samples)
    return hashlib.sha256(
        ("\n".join(digests)).encode("utf-8")
    ).hexdigest()


def dedupe_samples(samples: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    values: List[Dict[str, Any]] = []
    for sample in samples:
        digest = sample_digest(sample)
        if digest in seen:
            continue
        seen.add(digest)
        values.append(sample)
    return values


def _code_for(diff: str, path: str, line: int) -> Optional[str]:
    try:
        parsed = parse_unified_diff(diff)
    except Exception:
        return None
    for item in parsed.added_lines:
        if item.path == path and item.line == int(line):
            return item.content[:MAX_CODE_LEN]
    return None


def _neighborhood(diff: str, path: str, line: int, window: int = 2) -> List[str]:
    try:
        parsed = parse_unified_diff(diff)
    except Exception:
        return []
    lines = sorted(
        (item for item in parsed.added_lines if item.path == path),
        key=lambda item: item.line,
    )
    index = None
    for position, item in enumerate(lines):
        if item.line == int(line):
            index = position
            break
    if index is None:
        return []
    low = max(0, index - window)
    high = min(len(lines), index + window + 1)
    return [item.content[:MAX_CODE_LEN] for item in lines[low:high]]


def classify_from_corpus(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Positives from labelled corpus entries, negatives from clean cases.

    ``cases`` follow the evaluation-case shape: {"name","diff","expected":[...]}
    where each expected positive carries path/line/min_severity/risk_class.
    """
    samples: List[Dict[str, Any]] = []
    seen_lines = set()
    for case in cases:
        diff = case.get("diff", "")
        name = str(case.get("name", ""))
        expected = case.get("expected") or []
        if expected:
            for entry in expected:
                path, line = str(entry.get("path", "")), int(entry.get("line", 0))
                risk_class = str(entry.get("risk_class") or "").strip().lower()
                if risk_class not in AI_RISK_BY_CODE:
                    continue
                code = _code_for(diff, path, line)
                if not code:
                    continue
                key = (code, path, line)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                samples.append({
                    "kind": "classify",
                    "task_id": "",
                    "payload": {
                        "input": {
                            "path": path, "line": line, "code": code,
                            "context": _neighborhood(diff, path, line),
                        },
                        "response": {
                            "keep": True,
                            "risk_class": risk_class,
                            "severity": str(entry.get("min_severity") or "high"),
                        },
                    },
                    "provenance": {
                        "source": "corpus", "case": name,
                        "rule_id": str(entry.get("rule_id") or ""),
                    },
                })
        else:
            # Clean case: every added line is a negative preflight signal.
            try:
                parsed = parse_unified_diff(diff)
            except Exception:
                continue
            for item in parsed.added_lines:
                key = (item.content[:MAX_CODE_LEN], item.path, item.line)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                samples.append({
                    "kind": "classify",
                    "task_id": "",
                    "payload": {
                        "input": {
                            "path": item.path, "line": item.line,
                            "code": item.content[:MAX_CODE_LEN],
                            "context": _neighborhood(diff, item.path, item.line),
                        },
                        "response": {"keep": False},
                    },
                    "provenance": {"source": "corpus", "case": name},
                })
    return dedupe_samples(samples)


def classify_from_tasks(store, limit_tasks: int = 400) -> List[Dict[str, Any]]:
    """Positives from deterministic AI findings that survived the release gate."""
    samples: List[Dict[str, Any]] = []
    tasks = store.list_tasks(limit=limit_tasks)
    for task in tasks:
        task_id = str(task.get("id", ""))
        if task.get("state") != "SUCCESS":
            continue
        task = store.get(task_id)
        if not task:
            continue
        report = task.get("report") or {}
        diff = store.get_task_payload(task_id)
        if not diff:
            continue
        for finding in report.get("findings") or []:
            source = str(finding.get("source", ""))
            rule_id = str(finding.get("rule_id", ""))
            if not rule_id.startswith("AI-"):
                continue
            if "local-rule-scanner" not in source:
                continue
            risk_class = str(finding.get("risk_class") or "").strip().lower()
            if risk_class not in AI_RISK_BY_CODE:
                continue
            code = _code_for(
                diff, str(finding.get("path", "")), int(finding.get("line", 0))
            )
            if not code:
                continue
            samples.append({
                "kind": "classify",
                "task_id": task_id,
                "payload": {
                    "input": {
                        "path": finding.get("path"), "line": finding.get("line"),
                        "code": code,
                        "context": _neighborhood(
                            diff, str(finding.get("path", "")),
                            int(finding.get("line", 0)),
                        ),
                    },
                    "response": {
                        "keep": True,
                        "risk_class": risk_class,
                        "severity": str(finding.get("severity") or "high"),
                    },
                },
                "provenance": {
                    "source": "runtime-scanner", "task_id": task_id,
                    "rule_id": rule_id,
                },
            })
    return dedupe_samples(samples)


def _finding_key(finding: Dict[str, Any]) -> tuple:
    return (
        str(finding.get("rule_id", "")),
        str(finding.get("path", "")),
        int(finding.get("line", 0) or 0),
    )


def instruct_from_traces(
    store, vertical_only: bool = True, max_tasks: int = 200,
    traces_per_task: int = 8,
) -> List[Dict[str, Any]]:
    """Distil accepted worker turns into supervised finding-generation samples."""
    samples: List[Dict[str, Any]] = []
    tasks = store.list_tasks(limit=max_tasks)
    for task in tasks:
        task_id = str(task.get("id", ""))
        if task.get("state") != "SUCCESS":
            continue
        task = store.get(task_id)
        if not task:
            continue
        report = task.get("report") or {}
        accepted = {_finding_key(item) for item in report.get("findings") or []}
        if not accepted:
            continue
        traces = store.list_task_llm_traces(task_id)
        count = 0
        for trace in traces:
            if count >= traces_per_task:
                break
            if trace.get("event_type") != "llm":
                continue
            reply = trace.get("reply") or {}
            if str(reply.get("action", "")).strip().lower() != "final":
                continue
            findings = reply.get("findings") or []
            if not findings:
                continue
            if vertical_only and not any(
                item.get("risk_class") for item in findings
            ):
                continue
            if not any(_finding_key(item) in accepted for item in findings):
                continue
            prompt = trace.get("prompt") or {}
            system = str(prompt.get("system") or "")[:MAX_INSTRUCTION_LEN]
            user = str(prompt.get("user") or "")[:MAX_INSTRUCTION_LEN]
            if not system or not user:
                continue
            samples.append({
                "kind": "instruct",
                "task_id": task_id,
                "payload": {
                    "prompt": {"system": system, "user": user},
                    "response": reply,
                },
                "provenance": {
                    "source": "runtime-trajectory", "task_id": task_id,
                    "role": trace.get("role"), "step": trace.get("step"),
                },
            })
            count += 1
    return dedupe_samples(samples)


def episode_from_traces(
    store, min_quality: Optional[float] = None, max_tasks: int = 200,
    vertical_only: bool = False,
) -> List[Dict[str, Any]]:
    """Build episode-level samples: a task's trajectory meta + worker turn context
    supervised by the final accepted findings.

    This turns one full review into a single training episode (Hermes-style
    "trajectory as training asset") rather than isolated single-line samples.
    """
    samples: List[Dict[str, Any]] = []
    tasks = store.list_tasks(limit=max_tasks)
    for task in tasks:
        task_id = str(task.get("id", ""))
        if task.get("state") != "SUCCESS":
            continue
        if min_quality is not None:
            quality = task_quality(store, task_id)
            if quality is None or quality < float(min_quality):
                continue
        task = store.get(task_id)
        if not task:
            continue
        accepted = (task.get("report") or {}).get("findings") or []
        if not accepted:
            continue
        traces = store.list_task_llm_traces(task_id)
        steps: List[dict] = []
        anchor = None
        accepted_keys = {_finding_key(item) for item in accepted}
        for trace in traces:
            reply = trace.get("reply") or {}
            if trace.get("event_type") == "llm":
                steps.append({
                    "role": trace.get("role"), "step": trace.get("step"),
                    "action": str(reply.get("action", "")),
                })
                if anchor is None and str(reply.get("action", "")).lower() == "final":
                    keys = {
                        _finding_key(item) for item in reply.get("findings") or []
                    }
                    if keys & accepted_keys:
                        anchor = trace
            else:
                steps.append({
                    "role": trace.get("role"), "step": trace.get("step"),
                    "tool": trace.get("tool_name"), "action": "tool",
                })
        if anchor is None or not steps:
            continue
        prompt = anchor.get("prompt") or {}
        system = str(prompt.get("system") or "")[:MAX_INSTRUCTION_LEN]
        user = str(prompt.get("user") or "")[:MAX_INSTRUCTION_LEN]
        if not system or not user:
            continue
        if vertical_only and not any(item.get("risk_class") for item in accepted):
            continue
        samples.append({
            "kind": "episode",
            "task_id": task_id,
            "payload": {
                "input": {
                    "task_id": task_id, "steps": steps[:160],
                    "context": {"system": system, "user": user},
                },
                "response": {"action": "final", "findings": accepted},
            },
            "provenance": {
                "source": "runtime-trajectory-episode", "task_id": task_id,
                "role": anchor.get("role"), "step": anchor.get("step"),
            },
        })
    return dedupe_samples(samples)


def preference_from_feedback(store, max_tasks: int = 200) -> List[Dict[str, Any]]:
    """Chosen (accepted) vs rejected (false-positive) pairs from human feedback."""
    samples: List[Dict[str, Any]] = []
    cases = store.list_failure_cases()
    per_task: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        if case.get("category") != "false_positive":
            continue
        per_task.setdefault(str(case.get("task_id")), []).append(
            (case.get("payload") or {}).get("finding") or {}
        )
    seen_tasks = 0
    for task_id, rejected_findings in per_task.items():
        if seen_tasks >= max_tasks:
            break
        task = store.get(task_id)
        if not task or task.get("state") != "SUCCESS":
            continue
        report = task.get("report") or {}
        accepted = [
            item for item in report.get("findings") or [] if item.get("risk_class")
        ]
        if not accepted:
            continue
        if not accepted:
            continue
        traces = store.list_task_llm_traces(task_id)
        system = next(
            (str((trace.get("prompt") or {}).get("system") or "")[:MAX_INSTRUCTION_LEN]
             for trace in traces if trace.get("event_type") == "llm"
             and trace.get("prompt", {}).get("system")),
            PREFERENCE_INSTRUCTION,
        )
        chosen = accepted[0]
        for rejected in rejected_findings[:20]:
            if not rejected:
                continue
            if _finding_key(rejected) == _finding_key(chosen):
                continue
            samples.append({
                "kind": "preference",
                "task_id": task_id,
                "payload": {
                    "input": {"instruction": system},
                    "chosen": chosen,
                    "rejected": {
                        key: value for key, value in rejected.items()
                        if key in {
                            "rule_id", "severity", "title", "explanation",
                            "path", "line", "evidence", "risk_class",
                        }
                    },
                },
                "provenance": {
                    "source": "human-feedback", "task_id": task_id,
                    "feedback_category": "false_positive",
                },
            })
        seen_tasks += 1
    return dedupe_samples(samples)
