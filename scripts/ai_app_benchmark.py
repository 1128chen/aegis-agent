"""Benchmark the deterministic AI-application cold-path scanner vs the corpus.

Reports location-level precision/recall/F1 per risk class plus clean-case false
positives, so the "scanner teacher" baseline behind the classify dataset is
measurable.

Example:
    python scripts/ai_app_benchmark.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aegis.ai_risks import AiApplicationRuleReviewer  # noqa: E402
from aegis.diff_parser import parse_unified_diff  # noqa: E402
from aegis.flywheel.pipeline import load_corpus  # noqa: E402


def benchmark(corpus_path):
    scanner = AiApplicationRuleReviewer()
    cases = load_corpus(corpus_path)
    tp = {"total": 0, "by_class": {}}
    fp = {"total": 0, "by_class": {}}
    clean_false_positives = 0
    clean_lines = 0

    for case in cases:
        diff = case["diff"]
        expected = case.get("expected") or []
        parsed = parse_unified_diff(diff)
        produced = scanner.review(diff, parsed)
        expected_hits = {
            (str(e.get("path", "")), int(e.get("line", 0)))
            for e in expected
        }
        produced_hits = {(f.path, f.line) for f in produced}
        clean = not expected
        if clean:
            clean_lines += len(parsed.added_lines)
            clean_false_positives += len(produced)
            continue
        for finding in produced:
            key = (finding.path, finding.line)
            if key in expected_hits:
                tp["total"] += 1
                tp["by_class"].setdefault(finding.risk_class or "?", 0)
                tp["by_class"][finding.risk_class or "?"] += 1
            else:
                fp["total"] += 1
                fp["by_class"].setdefault(finding.risk_class or "?", 0)
                fp["by_class"][finding.risk_class or "?"] += 1

    precision = tp["total"] / max(1, tp["total"] + fp["total"])
    recall = tp["total"] / max(1, sum(len(case.get("expected") or [])
                                      for case in cases if case.get("expected")))
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    return {
        "cases": len(cases),
        "true_positives": tp,
        "false_positives": fp,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "clean_cases_fp": clean_false_positives,
        "clean_added_lines": clean_lines,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="")
    args = parser.parse_args(argv)
    import json
    print(json.dumps(benchmark(args.corpus), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
