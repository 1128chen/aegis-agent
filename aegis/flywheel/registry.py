"""Adapter version registry orchestration for the flywheel loop.

Mirrors the prompt-version registry (``store.skill_versions``) but for model
weights: a training run produces an adapter directory, which is registered as
the next ``model_adapters`` version for a layer and can be gated and activated.
The runtime then reads the active adapter when it rebuilds the reviewer.
"""
import hashlib
import json
import os
import uuid
from typing import Any, Dict, List, Optional

from ..store import utc_now
from . import train


def directory_sha256(directory: str) -> str:
    digest = hashlib.sha256()
    for root, _dirs, files in os.walk(directory):
        for filename in sorted(files):
            path = os.path.join(root, filename)
            try:
                with open(path, "rb") as handle:
                    digest.update(path.encode("utf-8"))
                    digest.update(handle.read())
            except OSError:
                continue
    return digest.hexdigest()


def adapter_gate(
    store, layer: str, version: int, tolerance: float = 0.02,
) -> Dict[str, Any]:
    """Non-regression gate for an adapter version vs the active baseline.

    Uses the stored per-version quality score (``1/(1+val_loss)``); a candidate
    within ``tolerance`` of the active adapter passes. Stronger E2E gating can
    be layered on top by running ``evaluation_v2`` against a holdout before
    calling ``activate_adapter``.
    """
    candidates = [item for item in store.list_adapters(layer)
                  if int(item.get("version") or 0) == int(version)]
    if not candidates:
        return {"present": False, "pass": False}
    candidate = candidates[0]
    active = store.get_active_adapter(layer)
    baseline = None
    if active and int(active.get("version") or 0) != int(version):
        baseline = float(active.get("score") or 0.0)
    candidate_score = float(candidate.get("score") or 0.0)
    passed = baseline is None or candidate_score >= baseline - float(tolerance)
    return {
        "present": True, "pass": bool(passed),
        "candidate_score": candidate_score, "baseline_score": baseline,
        "version": int(version),
    }


def quality_score(metrics: Dict[str, Any]) -> float:
    """A compact relative quality signal from training metrics.

    ``1/(1 + val_loss)`` is used so a lower validation loss yields a higher
    score; the actual acceptance gate for activation lives in the evaluation
    harness (see aegis.evaluation_v2), not here.
    """
    val_loss = metrics.get("val_loss")
    if val_loss is None:
        return 0.0
    return round(1.0 / (1.0 + float(val_loss)), 4)


def run_training_job(
    store, samples: List[Dict[str, Any]], hyper: Optional[Dict[str, Any]] = None,
    base_model: str = "", out_root: str = "", layer: str = "preflight",
    run_id: str = "", activate: bool = False,
) -> Dict[str, Any]:
    """Train on the given samples, register the run and the adapter version.

    Returns a dict with run_id, adapter version metadata and metrics. The
    adapter is stored as ``staging`` (or ``draft``) and is NOT activated unless
    ``activate`` is explicitly requested after evaluation gating.
    """
    samples = list(samples or [])
    if not samples:
        raise ValueError("no training samples")
    run_id = run_id or str(uuid.uuid4())
    run_type = "sft"
    dataset_hash = train_dataset_hash(samples)
    store.save_training_run({
        "id": run_id, "run_type": run_type, "base_model": base_model,
        "dataset_version": "", "dataset_hash": dataset_hash,
        "hyperparams": dict(hyper or train.default_hyperparams()),
        "metrics": {}, "status": "running", "created_at": utc_now(),
    })
    try:
        result = train.train_from_rows(
            rows=samples, hyper=hyper, base_model=base_model,
            out_root=out_root, run_id=run_id,
        )
    except Exception as exc:
        store.save_training_run({
            "id": run_id, "run_type": run_type, "base_model": base_model,
            "dataset_version": "", "dataset_hash": dataset_hash,
            "hyperparams": dict(hyper or train.default_hyperparams()),
            "metrics": {"error": str(exc)}, "status": "failed",
            "adapter_id": None, "created_at": utc_now(),
            "finished_at": utc_now(),
        })
        raise
    adapter_dir = str(result["adapter_dir"])
    metrics = dict(result["metrics"])
    sha = directory_sha256(adapter_dir)
    version = store_versions(store, layer) + 1
    adapter = {
        "base_model": result.get("base_model"),
        "adapter_sha256": sha,
        "adapter_dir": adapter_dir,
        "serving_model_id": "aegis-%s-v%d" % (layer, version),
        "metrics": {"val_loss": metrics.get("val_loss"),
                    "train_loss": metrics.get("train_loss"),
                    "duration_s": metrics.get("duration_s")},
        "status": "staging" if not activate else "draft",
    }
    store.save_adapter(
        layer, adapter, score=quality_score(metrics),
        activate=False, skill_name="llm-review",
    )
    store.save_training_run({
        "id": run_id, "run_type": run_type, "base_model": result.get("base_model"),
        "dataset_version": version, "dataset_hash": dataset_hash,
        "hyperparams": dict(result["hyperparams"]), "metrics": metrics,
        "status": "completed", "adapter_id": "layer=%s version=%d" % (layer, version),
        "created_at": utc_now(), "finished_at": utc_now(),
    })
    return {
        "run_id": run_id, "layer": layer, "adapter_version": version,
        "adapter_dir": adapter_dir, "adapter_sha256": sha,
        "score": quality_score(metrics), "metrics": metrics,
    }


def store_versions(store, layer: str) -> int:
    adapters = store.list_adapters(layer)
    return int((adapters[0].get("version") if adapters else 0))


def train_dataset_hash(samples: List[Dict[str, Any]]) -> str:
    payloads = [
        json.dumps(sample.get("payload") or sample, ensure_ascii=False,
                   sort_keys=True, separators=(",", ":"))
        for sample in samples
    ]
    return hashlib.sha256("\n".join(sorted(payloads)).encode("utf-8")).hexdigest()


def evaluate_and_maybe_activate(store, layer: str, version: int) -> Dict[str, Any]:
    """Gate and activate an adapter version (no-op guard until an eval harness
    is attached). Marks the adapter active so the runtime can pick it up."""
    ok = store.activate_adapter(layer, version)
    return {"activated": ok, "layer": layer, "version": version}
