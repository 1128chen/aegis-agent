"""Automatic scoring/annotation on top of the trajectory store.

Labels feed the dataset pipeline and the dashboard. Rule-derived labels
(``auto_rule``) are the cheapest trustworthy signal: a deterministic AI finding
that passed the release gate is, by construction, a true positive for the risk
class it was designed to detect.
"""
from typing import Any, Dict, List

from ..ai_risks import AI_RISK_BY_CODE


def auto_label_runtime_findings(store, limit_tasks: int = 400) -> Dict[str, Any]:
    """Write a ``finding_accept`` auto_rule label per gated deterministic finding."""
    counters: Dict[str, int] = {"labeled": 0, "already": 0, "skipped": 0}
    for task in store.list_tasks(limit=limit_tasks):
        task_id = str(task.get("id", ""))
        if task.get("state") != "SUCCESS":
            counters["skipped"] += 1
            continue
        task = store.get(task_id)
        if not task:
            counters["skipped"] += 1
            continue
        existing = {
            (item.get("dimension"), item.get("label"), str(item.get("metadata", {}).get("rule_id", "")))
            for item in store.list_task_labels(task_id)
        }
        report = task.get("report") or {}
        for finding in report.get("findings") or []:
            source = str(finding.get("source", ""))
            rule_id = str(finding.get("rule_id", ""))
            risk_class = str(finding.get("risk_class") or "").strip().lower()
            if not rule_id.startswith("AI-") or "local-rule-scanner" not in source:
                continue
            if risk_class not in AI_RISK_BY_CODE:
                continue
            if ("finding_accept", "accept", rule_id) in existing:
                counters["already"] += 1
                continue
            store.save_task_label(
                task_id, "finding_accept", score=0.95, label="accept",
                scored_by="auto_rule",
                note="deterministic AI finding passed the release gate",
                metadata={"rule_id": rule_id, "risk_class": risk_class},
            )
            counters["labeled"] += 1
    return counters


def auto_label_preflight_hits(
    store, classified: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Record preflight-classification agreement as a task-level label."""
    counters: Dict[str, int] = {"labeled": 0}
    for sample in classified:
        task_id = sample.get("task_id")
        if not task_id:
            continue
        response = sample.get("payload", {}).get("response") or {}
        if not response.get("keep"):
            continue
        risk_class = response.get("risk_class")
        store.save_task_label(
            task_id, "high_risk_recall", score=1.0, label="pass",
            scored_by="auto_rule",
            note="preflight classified the deterministic positive correctly",
            metadata={"risk_class": risk_class},
        )
        counters["labeled"] += 1
    return counters
