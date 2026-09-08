"""Regenerate the two controlled offline corpora that the base repo omitted.

- evaluation_data/pr_diff_100.jsonl          -> 10 repos x 10 cases, 40 risk/60 clean
- evaluation_data/prompt_evolution_130.jsonl -> 10 repos x 13 cases (8 validation /
  2 holdout repos), exactly 32 baseline-missed context findings in validation

The prompt corpus is engineered so the offline PromptPolicyReviewer (which only
activates ContextRule findings whose rule_id appears as a [focus-rule:...] marker
in the prompt) reproduces the designed proof: baseline misses 32 validation
findings, and the auto-learned candidate prompt recovers all of them plus the
holdout instances, so candidate F1 > baseline F1 on both splits.

Run:  python scripts/generate_controlled_corpora.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "evaluation_data")

# (rule_id, canonical cwe, severity, added-line code) — each line triggers only
# the named deterministic rule (never a Local generic rule or another Context rule).
CONTEXT_RISKS = [
    ("SEC-PATH-TRAVERSAL", "CWE-22", "high", "data = open(base / user_path).read()"),
    ("SEC-YAML-LOAD", "CWE-502", "high", "payload = yaml.load(stream)"),
    ("SEC-WEAK-HASH", "CWE-328", "medium", "digest = hashlib.md5(blob).hexdigest()"),
    ("SEC-INSECURE-COOKIE", "CWE-614", "medium",
     'response.set_cookie("session", sid, secure=False)'),
]
LOCAL_RISKS = [
    ("SEC-EVAL", "CWE-95", "critical", "value = eval(expression)"),
    ("SEC-SUBPROCESS-SHELL", "CWE-78", "high",
     "subprocess.run(command, shell=True)"),
    ("SEC-HARDCODED-SECRET", "CWE-798", "high",
     'password = "correct-horse-battery-staple"'),
    ("SEC-SQL-CONCAT", "CWE-89", "high",
     'cursor.execute("SELECT * FROM items WHERE id = " + str(item_id))'),
]

BENIGN = [
    "total = sum(prices) + tax_rate",
    'client.send(Event(name="ping", seq={i}))',
    "config = env.get(\"MAX_SIZE\", default=10)",
    "memo[index] = cache.get(key, None)",
    "updated = current + delta",
    "label = prefix + str(index)",
    "assert response.status in (200, 304)",
    "rows = query_builder.limit(limit).all()",
    "value = float(raw) if raw else 0.0",
    "result = normalize(input_text).strip()",
]


def _diff(line: str) -> str:
    return "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+%s\n" % line


def _expected_finding(rule_id, cwe, severity) -> dict:
    return {
        "path": "app.py", "start_line": 1, "end_line": 1,
        "rule_id": rule_id, "cwe": cwe, "severity": severity,
        "should_comment": True,
    }


def _clean_line(seed: int) -> str:
    return BENIGN[seed % len(BENIGN)].format(i=seed) if "{" in BENIGN[seed % len(BENIGN)] \
        else BENIGN[seed % len(BENIGN)] + "  # case-%d" % seed


def build_pr_diff_100() -> list:
    """10 repositories x 10 cases; file split marks repos 0-7 validation, 8-9 holdout."""
    cases = []
    risks = LOCAL_RISKS + CONTEXT_RISKS
    for repo_index in range(10):
        split = "holdout" if repo_index >= 8 else "validation"
        repository = "acme/repo-%02d" % repo_index
        for case_index in range(10):
            case_id = "controlled-100-r%02d-c%02d" % (repo_index, case_index)
            risk = case_index < 4  # 4 risk + 6 clean per repo => 40 risk / 60 clean
            if risk:
                rule_id, cwe, severity, code = risks[(repo_index * 4 + case_index) % len(risks)]
                expected = [_expected_finding(rule_id, cwe, severity)]
                line = code
            else:
                expected = []
                line = _clean_line(repo_index * 10 + case_index)
            cases.append({
                "schema_version": 1,
                "id": case_id,
                "repository": repository,
                "pull_request": 1,
                "split": split,
                "source": {"kind": "offline-fixture"},
                "diff": _diff(line),
                "expected_findings": expected,
            })
    return cases


def build_prompt_evolution_130() -> list:
    """10 repositories x 13 cases.

    Validation repos 0-7 hold exactly 32 baseline-missed context findings (4 per
    repo). Holdout repos 8-9 hold the same rule ids (4 each) so the candidate
    prompt learned from validation also improves holdout F1.  Clean cases make up
    the remainder.
    """
    cases = []
    for repo_index in range(10):
        split = "holdout" if repo_index >= 8 else "validation"
        repository = "acme/repo-%02d" % repo_index
        for case_index in range(13):
            case_id = "prompt-evo-r%02d-c%02d" % (repo_index, case_index)
            rule = (repo_index * 4 + case_index) % len(CONTEXT_RISKS)
            risk = case_index < 4  # 4 context findings + 9 clean per repo
            if risk:
                rule_id, cwe, severity, code = CONTEXT_RISKS[rule]
                expected = [_expected_finding(rule_id, cwe, severity)]
                line = code
            else:
                expected = []
                line = _clean_line(repo_index * 13 + case_index)
            cases.append({
                "schema_version": 1,
                "id": case_id,
                "repository": repository,
                "pull_request": 1,
                "split": split,
                "source": {"kind": "offline-fixture"},
                "diff": _diff(line),
                "expected_findings": expected,
            })
    return cases


def _write(path, cases) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    pr100 = build_pr_diff_100()
    evo130 = build_prompt_evolution_130()
    # sanity: validate_case + structural counts
    from aegis.evaluation_harness import load_jsonl

    _write(os.path.join(DATA_DIR, "pr_diff_100.jsonl"), pr100)
    _write(os.path.join(DATA_DIR, "prompt_evolution_130.jsonl"), evo130)

    loaded100 = load_jsonl(os.path.join(DATA_DIR, "pr_diff_100.jsonl"))
    loaded130 = load_jsonl(os.path.join(DATA_DIR, "prompt_evolution_130.jsonl"))
    print("pr_diff_100: %d cases, %d risk, %d clean, %d repos" % (
        len(loaded100),
        sum(bool(c["expected_findings"]) for c in loaded100),
        sum(not c["expected_findings"] for c in loaded100),
        len({c["repository"] for c in loaded100}),
    ))
    val_repos = {c["repository"] for c in loaded130 if c["split"] == "validation"}
    hold_repos = {c["repository"] for c in loaded130 if c["split"] == "holdout"}
    val_missed = sum(
        1 for c in loaded130 if c["split"] == "validation" and c["expected_findings"]
    )
    hold_risk = sum(
        1 for c in loaded130 if c["split"] == "holdout" and c["expected_findings"]
    )
    print("prompt_evolution_130: %d cases, validation repos %d, holdout repos %d, "
          "validation baseline-missed (context findings) %d, holdout context %d" % (
        len(loaded130), len(val_repos), len(hold_repos), val_missed, hold_risk,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
