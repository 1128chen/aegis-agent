"""Experience mining: turn a completed review into a reusable Skill learning.

Hermes-style "creates skills from experience": after a review confirms a
vertical (AI/LLM application security) defect, we mine a suggestion that can be
rendered into an evolved ``ai-app-security`` SKILL.md artifact. Accepting a
suggestion registers a new artifact version (overlay on the disk skill); the
existing artifact version chain supports activation and rollback.
"""
import os
import re
from typing import Any, Dict, List, Optional

RULE_MARKER = re.compile(r"^[A-Z][A-Z0-9_-]{1,79}$")
LEARNED_BLOCK = "<!-- aegis:learned:{rule}:start -->\n- {note}\n<!-- aegis:learned:{rule}:end -->"

DEFAULT_SKILL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "ai-app-security"
)


def _marker(value: str, fallback: str = "AI") -> Optional[str]:
    candidate = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).upper()
    candidate = candidate.strip("-")
    if not RULE_MARKER.fullmatch(candidate):
        candidate = re.sub(r"[^A-Za-z0-9_-]+", "-", str(fallback).upper()).strip("-")
    if not RULE_MARKER.fullmatch(candidate):
        return None
    return candidate


def suggestions_from_report(
    repository: str, report: Dict[str, Any], task_id: str = "",
) -> List[Dict[str, Any]]:
    """Cluster accepted AI findings of one task into suggestion payloads."""
    clusters: Dict[tuple, dict] = {}
    for finding in report.get("findings") or []:
        risk_class = str(finding.get("risk_class") or "").strip().lower()
        if not risk_class:
            continue
        key = (str(finding.get("rule_id") or "REVIEW")[:80], risk_class)
        bucket = clusters.setdefault(key, {"count": 0, "title": "", "rationale": ""})
        bucket["count"] += 1
        if not bucket["title"]:
            bucket["title"] = str(finding.get("title") or "Learned review rule")[:200]
        explanation = str(finding.get("explanation") or "")
        if explanation and len(bucket["rationale"]) < 400:
            bucket["rationale"] = explanation[:400]
    suggestions = []
    for (rule_id, risk_class), bucket in clusters.items():
        marker = _marker(rule_id)
        if not marker:
            continue
        suggestions.append({
            "task_id": str(task_id)[:64],
            "repository": str(repository)[:250],
            "rule_id": rule_id,
            "risk_class": risk_class,
            "marker": marker,
            "title": bucket["title"],
            "rationale": bucket["rationale"],
            "count": bucket["count"],
            "finding_count": bucket["count"],
            "severity_hint": risk_class,
        })
    return suggestions


def render_learned_block(suggestion: Dict[str, Any]) -> str:
    note = "Confirmed %s experience from %s: %s" % (
        suggestion.get("risk_class", "review"),
        suggestion.get("repository") or "a repository",
        suggestion.get("rationale") or suggestion.get("title", ""),
    )
    return LEARNED_BLOCK.format(rule=suggestion["marker"], note=note)


def read_base_skill_md(skill_dir: str = "") -> str:
    directory = skill_dir or DEFAULT_SKILL_DIR
    path = os.path.join(directory, "SKILL.md")
    if not os.path.isfile(path):
        raise FileNotFoundError("base skill not found: %s" % path)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def build_candidate_artifact(
    suggestion: Dict[str, Any], base_md: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Render a suggestion into an evolved SKILL.md artifact (or None)."""
    marker = _marker(suggestion.get("rule_id", ""), suggestion.get("risk_class", "AI"))
    if not marker:
        return None
    base_md = base_md or read_base_skill_md()
    if marker in base_md:
        return None  # already learned
    block = render_learned_block({**suggestion, "marker": marker})
    evolved = base_md.rstrip() + "\n\n" + block + "\n"
    return {
        "schema_version": 2,
        "format": "agent-skill",
        "name": "ai-app-security",
        "description": "Evolved ai-app-security skill (learning overlay).",
        "files": {"SKILL.md": evolved},
    }


def mine_task(store, task_id: str, repository: str = "") -> Dict[str, Any]:
    """Mine one completed task into pending experience suggestions."""
    task = store.get(task_id)
    if not task or task.get("state") != "SUCCESS":
        return {"task_id": task_id, "mined": 0, "skipped": 0}
    report = task.get("report") or {}
    repository = repository or str(task.get("repository") or "unknown")
    mined = 0
    skipped = 0
    for suggestion in suggestions_from_report(repository, report, task_id):
        if store.has_experience_suggestion(repository, suggestion["rule_id"], task_id):
            skipped += 1
            continue
        store.save_experience_suggestion(
            repository, suggestion["rule_id"], suggestion["risk_class"],
            suggestion["title"], suggestion["rationale"],
            suggestion, task_id,
        )
        mined += 1
    return {"task_id": task_id, "mined": mined, "skipped": skipped}
