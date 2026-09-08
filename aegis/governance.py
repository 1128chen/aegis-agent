"""Periodic PR governance scans over stored reviews (Hermes-style scheduled audits).

Aggregates the already-run reviews of a repository into a governance report:
PR volume, defect distribution by rule / AI risk / severity, and the
false-positive rules users have flagged. Deterministic (no extra LLM calls) so
it can run unattended on a schedule and be pushed to a webhook.
"""
import uuid
from collections import Counter
from typing import Any, Dict

from .store import utc_now


def run_repo_scan(store, tenant_id: str, repository: str, since: str = "") -> Dict[str, Any]:
    reports = store.list_repository_reports(tenant_id, repository, 1000)
    window = []
    for item in reports:
        if since and str(item.get("created_at") or "") < since:
            continue
        window.append(item)
    rules: Counter = Counter()
    risks: Counter = Counter()
    severities: Counter = Counter()
    for item in window:
        for finding in (item.get("report") or {}).get("findings") or []:
            rules[str(finding.get("rule_id", ""))] += 1
            severity = str(finding.get("severity", "")).lower()
            severities[severity] += 1
            risk = str(finding.get("risk_class") or "").strip().lower()
            if risk:
                risks[risk] += 1
    false_positives = [
        case for case in store.list_repository_failure_cases(
            tenant_id, repository, 1000,
        )
        if case.get("category") == "false_positive"
        and (not since or str(case.get("created_at") or "") >= since)
    ]
    high_critical = severities["critical"] + severities["high"]
    report = {
        "repository": repository,
        "since": since or "all-time",
        "scanned_at": utc_now(),
        "prs": len(window),
        "findings": int(sum(rules.values())),
        "high_critical": int(high_critical),
        "top_rules": [{"rule_id": rule, "count": count}
                      for rule, count in rules.most_common(8)],
        "ai_risks": [{"risk_class": risk, "count": count}
                     for risk, count in risks.most_common(8)],
        "severity_distribution": dict(severities),
        "false_positive_feedback": len(false_positives),
        "false_positive_rule_ids": sorted({
            str(((case.get("payload") or {}).get("finding") or {}).get("rule_id", ""))
            for case in false_positives if case.get("category") == "false_positive"
        }),
    }
    report["text"] = (
        "Governance scan of %s (%s): %d PR(s), %d finding(s), %d high/critical, "
        "%d false-positive feedback." % (
            repository, report["since"], report["prs"], report["findings"],
            report["high_critical"], report["false_positive_feedback"],
        )
    )
    return report


def persist_report(store, report: Dict[str, Any], report_id: str = "") -> str:
    report_id = report_id or "gov-%s" % str(uuid.uuid4())
    store.save_governance_report(
        report_id, str(report.get("repository", "")), str(report.get("since", "")),
        report,
    )
    return report_id
