"""Lightweight automated data pipeline: traces/labels -> cleaned LoRA dataset.

The pipeline is idempotent on ``dataset_version`` (a content hash): re-running
with the same inputs purges and rewrites the ``selected`` pool identically.
Steps mirror the plan: pull -> clean -> dedup -> format -> export -> manifest.
"""
import json
import os
from typing import Any, Dict, List, Optional

from .datasets import (
    SAMPLE_KINDS, classify_from_corpus, classify_from_tasks,
    dataset_version, dedupe_samples, episode_from_traces,
    instruct_from_traces, preference_from_feedback,
)
from .curation import curate_tasks
from .labels import auto_label_runtime_findings

DEFAULT_CORPUS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "evaluation_data", "ai_app_pr_diff.jsonl",
)

REQUIRED_FIELDS = {
    "classify": ("input", "response"),
    "instruct": ("prompt", "response"),
    "preference": ("input", "chosen", "rejected"),
    "episode": ("input", "response"),
}


def validate_sample(sample: Dict[str, Any]) -> Optional[str]:
    kind = str(sample.get("kind", ""))
    if kind not in SAMPLE_KINDS:
        return "unknown kind"
    payload = sample.get("payload")
    if not isinstance(payload, dict):
        return "payload must be an object"
    for field in REQUIRED_FIELDS[kind]:
        if field not in payload:
            return "missing payload field: %s" % field
    return None


def clean_samples(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in samples if validate_sample(item) is None]


def load_corpus(path: str = "") -> List[Dict[str, Any]]:
    corpus_path = path or DEFAULT_CORPUS
    cases = []
    if not os.path.isfile(corpus_path):
        return cases
    with open(corpus_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                case = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(case, dict) and case.get("diff") is not None:
                cases.append(case)
    return cases


def build_pool(
    store, options: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    options = dict(options or {})
    pool: Dict[str, List[Dict[str, Any]]] = {}
    enabled = {
        kind: bool(options.get(kind, True)) for kind in SAMPLE_KINDS
    }
    max_tasks = int(options.get("max_tasks", 400))
    vertical_only = bool(options.get("vertical_only", True))
    budget = int(options.get("sample_budget", 4000))

    if enabled["classify"]:
        corpus = load_corpus(str(options.get("corpus_path", "")))
        samples = classify_from_corpus(corpus) + classify_from_tasks(
            store, limit_tasks=max_tasks
        )
        pool["classify"] = dedupe_samples(clean_samples(samples))[:budget]

    if enabled["instruct"]:
        pool["instruct"] = instruct_from_traces(
            store, vertical_only=vertical_only, max_tasks=max_tasks
        )[:budget]

    if enabled["preference"]:
        pool["preference"] = preference_from_feedback(
            store, max_tasks=max_tasks
        )[:budget]

    if enabled["episode"]:
        min_quality = options.get("min_quality")
        pool["episode"] = episode_from_traces(
            store, min_quality=min_quality, max_tasks=max_tasks,
        )[:budget]
    return pool


def export_pool(
    store, pool: Dict[str, List[Dict[str, Any]]],
    output_dir: str = "", options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Export the pool as jsonl + manifest and register it in the sft_samples pool."""
    output_dir = output_dir or str(options.get("output_dir") or "data/sft")
    os.makedirs(output_dir, exist_ok=True)
    flat = [
        item for kind in SAMPLE_KINDS for item in pool.get(kind, [])
        if validate_sample(item) is None
    ]
    version = dataset_version(flat)
    short = version[:8]
    manifest_path = os.path.join(output_dir, "manifest-%s.jsonl" % short)
    payload_lines = []
    with open(manifest_path, "w", encoding="utf-8") as handle:
        for sample in flat:
            line = {
                "kind": sample["kind"],
                "task_id": sample.get("task_id", ""),
                "payload": sample["payload"],
                "provenance": sample.get("provenance", {}),
            }
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            payload_lines.append(line)

    counts: Dict[str, int] = {}
    for kind in SAMPLE_KINDS:
        counts[kind] = sum(1 for item in pool.get(kind, []) if validate_sample(item) is None)
    risk_distribution = {}
    keep_distribution = {"true": 0, "false": 0}
    for kind in SAMPLE_KINDS:
        for sample in pool.get(kind, []):
            if validate_sample(sample) is not None:
                continue
            response = sample["payload"].get("response") or {}
            risk = response.get("risk_class")
            if risk:
                risk_distribution[risk] = risk_distribution.get(risk, 0) + 1
            if "keep" in response:
                keep_distribution[
                    "true" if response.get("keep") else "false"
                ] += 1

    store.purge_sft_pool(version)
    selected = store.record_sft_samples(
        flat, dataset_version=version, status="selected"
    )
    summary = {
        "dataset_version": version,
        "dataset_short": short,
        "total": len(flat),
        "counts": counts,
        "risk_distribution": risk_distribution,
        "keep_distribution": keep_distribution,
        "manifest_path": manifest_path,
        "sft_samples_registered": selected,
        "bytes": int(os.path.getsize(manifest_path)),
    }
    with open(os.path.join(output_dir, "manifest-%s.json" % short), "w",
              encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary


def run_pipeline(
    store, output_dir: str = "", options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """End-to-end pipeline run. Idempotent; returns the dataset manifest."""
    options = dict(options or {})
    pool = build_pool(store, options)
    label_stats = auto_label_runtime_findings(store)
    curation_stats = curate_tasks(store)
    manifest = export_pool(store, pool, output_dir=output_dir, options=options)
    manifest["labeling"] = label_stats
    manifest["curation"] = {
        "scored": curation_stats["scored"],
        "already": curation_stats["already"],
        "skipped": curation_stats["skipped"],
    }
    manifest["enabled_kinds"] = [
        kind for kind in SAMPLE_KINDS if manifest["counts"].get(kind)
    ]
    return manifest
