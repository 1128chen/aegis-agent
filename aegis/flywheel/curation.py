"""Trajectory curation: score each completed task as a training-data candidate.

Hermes-style data selection: not every run is worth training on. We derive a
deterministic 0..1 quality score from observable signals (release gate outcome,
human acceptance labels, trajectory coverage) and record it as a
``trajectory_quality`` auto_rule label so dashboards and dataset builders can
filter on it.
"""
from typing import Any, Dict, Optional, Tuple

QUALITY_DIMENSION = "trajectory_quality"


def task_quality_score(store, task: Dict[str, Any]) -> Tuple[float, list]:
    """Deterministic quality score for one completed task."""
    reasons: list = []
    if str(task.get("state", "")).upper() != "SUCCESS" or not task.get("report"):
        return 0.0, ["not a successful review"]
    report = task.get("report") or {}
    accepted = report.get("findings") or []
    score = 0.0
    if accepted:
        score += 0.5
        reasons.append("accepted findings: %d" % len(accepted))
    labels = store.list_task_labels(str(task["id"]))
    human_accept = any(
        item.get("dimension") == "finding_accept" and item.get("label") == "accept"
        and item.get("scored_by") == "human"
        for item in labels
    )
    if human_accept:
        score += 0.3
        reasons.append("human accepted findings")
    traces = store.list_task_llm_traces(str(task["id"]), limit=1000)
    if traces:
        score += 0.2
        reasons.append("content trajectory captured (%d)" % len(traces))
    return round(min(1.0, score), 3), reasons


def curate_tasks(store, limit_tasks: int = 400) -> Dict[str, Any]:
    """Score completed tasks and persist a trajectory_quality label per task."""
    stats = {"scored": 0, "already": 0, "skipped": 0, "labels": []}
    for task in store.list_tasks(limit=limit_tasks):
        task_id = str(task.get("id", ""))
        if str(task.get("state", "")).upper() != "SUCCESS":
            stats["skipped"] += 1
            continue
        if any(
            item.get("dimension") == QUALITY_DIMENSION
            for item in store.list_task_labels(task_id)
        ):
            stats["already"] += 1
            continue
        full = store.get(task_id)
        if not full:
            stats["skipped"] += 1
            continue
        score, reasons = task_quality_score(store, full)
        store.save_task_label(
            task_id, QUALITY_DIMENSION, score=score,
            label=str(round(score, 2)), scored_by="auto_rule",
            note="deterministic trajectory quality curation",
            metadata={"reasons": reasons},
        )
        stats["scored"] += 1
        stats["labels"].append({"task_id": task_id, "score": score})
    return stats


def task_quality(store, task_id: str) -> Optional[float]:
    """Latest curation score for a task, or None when not yet curated."""
    for item in store.list_task_labels(str(task_id)):
        if item.get("dimension") == QUALITY_DIMENSION and item.get("score") is not None:
            return float(item["score"])
    return None
