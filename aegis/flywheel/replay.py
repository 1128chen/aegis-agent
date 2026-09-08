"""Trajectory replay consistency guard (deterministic, no model needed).

Every accepted finding that a model role produced should be visible somewhere in
the captured content trajectory. After changing a prompt/model/adapter this
check re-runs over stored tasks to confirm the trajectory store still explains
the final report — a cheap data-integrity regression guard that complements the
model-level evaluation harness.
"""
from typing import Any, Dict

WORKER_SOURCES = {
    "security", "security@", "correctness-reliability", "lead", "critic",
}


def _is_scanner(finding: Dict[str, Any]) -> bool:
    source = str(finding.get("source", "")).lower()
    return "local-rule-scanner" in source or source in {"", "unknown"}


def _finding_key(finding: Dict[str, Any]) -> tuple:
    return (
        str(finding.get("rule_id", "")),
        str(finding.get("path", "")),
        int(finding.get("line", 0) or 0),
    )


def replay_consistency(store, limit_tasks: int = 200) -> Dict[str, Any]:
    checked = consistent = 0
    mismatches = []
    for task in store.list_tasks(limit=limit_tasks):
        if task.get("state") != "SUCCESS":
            continue
        task = store.get(str(task["id"]))
        if not task:
            continue
        report = task.get("report") or {}
        accepted = report.get("findings") or []
        worker_sourced = [item for item in accepted if not _is_scanner(item)]
        if not worker_sourced:
            continue
        traces = store.list_task_llm_traces(str(task["id"]))
        observed = set()
        for trace in traces:
            if trace.get("event_type") != "llm":
                continue
            reply = trace.get("reply") or {}
            if str(reply.get("action", "")).lower() != "final":
                continue
            for finding in reply.get("findings") or []:
                observed.add(_finding_key(finding))
        missing = [item for item in worker_sourced if _finding_key(item) not in observed]
        checked += 1
        if missing:
            mismatches.append({
                "task_id": str(task["id"]),
                "missing": len(missing),
                "sample": {
                    "rule_id": missing[0].get("rule_id"),
                    "path": missing[0].get("path"),
                    "line": missing[0].get("line"),
                    "source": missing[0].get("source"),
                },
            })
        else:
            consistent += 1
    return {
        "checked": checked,
        "consistent": consistent,
        "inconsistent": len(mismatches),
        "mismatches": mismatches[:10],
        "guard_passed": len(mismatches) == 0,
    }
