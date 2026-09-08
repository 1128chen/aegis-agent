"""Hermes-style cross-session memory: FTS review history + per-repo digest.

A review agent should "remember" how a repository was reviewed before. We index
every completed review into a full-text store (searchable cross-session) and
build a per-repository digest (top rules / AI risks / historically false-positive
rules) that is injected into the agent's recall context.
"""
from collections import Counter
from typing import Any, Dict, List, Optional

from .memory import MemoryManager

DIGEST_KIND = "repository_digest"


def review_search_text(repository: str, report: Dict[str, Any]) -> str:
    parts = [repository, str(report.get("summary") or "")]
    for finding in report.get("findings") or []:
        risk = finding.get("risk_class")
        parts.append(
            "%s %s %s %s:%s %s %s" % (
                finding.get("rule_id", ""), risk or "", finding.get("severity", ""),
                finding.get("path", ""), finding.get("line", 0),
                finding.get("title", ""),
                str(finding.get("evidence", ""))[:120],
            )
        )
    return "\n".join(part for part in parts if part).strip()


def repository_digest(
    store, tenant_id: str, repository: str, limit: int = 300,
) -> Dict[str, Any]:
    reports = store.list_repository_reports(tenant_id, repository, limit)
    rules: Counter = Counter()
    risks: Counter = Counter()
    high = 0
    for item in reports:
        for finding in (item.get("report") or {}).get("findings") or []:
            rules[str(finding.get("rule_id", ""))] += 1
            risk = str(finding.get("risk_class") or "").strip().lower()
            if risk:
                risks[risk] += 1
            if str(finding.get("severity", "")).lower() in {"high", "critical"}:
                high += 1
    false_positive_rules = sorted({
        str(((case.get("payload") or {}).get("finding") or {}).get("rule_id", ""))
        for case in store.list_repository_failure_cases(tenant_id, repository, 500)
        if case.get("category") == "false_positive"
    })
    top_rules = [{"rule_id": rule, "count": count}
                 for rule, count in rules.most_common(6)]
    top_risks = [{"risk_class": risk, "count": count}
                 for risk, count in risks.most_common(6)]
    text = "Repository %s history digest: %d reviewed PR(s), %d finding(s), %d high/critical." % (
        repository, len(reports), sum(rules.values()), high,
    )
    if top_rules:
        text += " Most common rules: " + ", ".join(
            "%s(%d)" % (item["rule_id"], item["count"]) for item in top_rules
        ) + "."
    if top_risks:
        text += " AI risks: " + ", ".join(
            "%s(%d)" % (item["risk_class"], item["count"]) for item in top_risks
        ) + "."
    if false_positive_rules:
        text += " Historically false-positive rules: " + ", ".join(false_positive_rules) + "."
    return {
        "repository": repository, "reviewed_prs": len(reports),
        "findings": int(sum(rules.values())), "high_critical": high,
        "top_rules": top_rules, "ai_risks": top_risks,
        "false_positive_rule_ids": false_positive_rules,
        "text": text,
    }


class DigestAugmentedMemory(MemoryManager):
    """Wraps MemoryManager to append a repository digest to each recall.

    The digest is an ephemeral, computed memory (never persisted) so the agent
    sees "how this repo was reviewed before" without us re-fitting the store.
    """

    def __init__(self, base: MemoryManager, store):
        self._base = base
        self._store = store

    def __getattr__(self, name: str):
        return getattr(self._base, name)

    def recall(
        self, tenant_id: str, repository: str, query: str,
        scopes=("semantic", "episodic"), limit=None, task_id: str = "",
    ) -> List[Dict[str, Any]]:
        values = self._base.recall(
            tenant_id, repository, query, scopes=scopes, limit=limit,
            task_id=task_id,
        )
        try:
            if not self._base.enabled:
                return values
            digest = repository_digest(self._store, tenant_id, repository)
            if digest.get("reviewed_prs"):
                values = list(values) + [{
                    "id": "digest:%s:%s" % (tenant_id, repository),
                    "scope": "episodic", "kind": DIGEST_KIND,
                    "content": digest["text"], "keywords": [],
                    "importance": 0.95, "created_at": "",
                    "metadata": digest, "recall_score": 1.0,
                }]
        except Exception:
            return values
        return values
