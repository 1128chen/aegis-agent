"""Background flywheel scheduler.

Consumes the (previously unused) ``continuous_eval_seconds`` cadence: when
enough new labels accumulate it runs the data pipeline and, if auto-training is
enabled, registers a fresh staging adapter. Off by default; the dashboard / CLI
expose the same operations for manual triggering.
"""
import threading
import time
from typing import Any, Dict, Optional

from ..store import utc_now


class FlywheelScheduler:
    def __init__(self, store, settings, run_pipeline, run_training_job,
                 governance_runner=None):
        self.store = store
        self.settings = settings
        self._run_pipeline = run_pipeline
        self._run_training_job = run_training_job
        self._governance_runner = governance_runner
        self._interval = max(0, int(settings.continuous_eval_seconds))
        self._min_new_labels = max(1, int(settings.flywheel_min_new_labels))
        self._last_labels = self.store.count_task_labels()
        self._running = False
        self._thread = None
        self.last_attempt: Dict[str, Any] = {}

    def start(self) -> None:
        if self._running or self._interval <= 0 or not self.settings.flywheel_auto_train:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self) -> None:
        while self._running:
            time.sleep(min(self._interval, 300))
            if not self._running:
                break
            try:
                self.tick()
            except Exception as exc:  # the scheduler must never kill the service
                self.last_attempt = {"at": utc_now(), "error": str(exc)[:1000]}

    def tick(self) -> Dict[str, Any]:
        """Periodic governance scan, then data-and-training when labels accrue."""
        now = self.store.count_task_labels()
        new_labels = max(0, now - self._last_labels)
        result = {
            "at": utc_now(), "new_labels_since_last": new_labels,
            "min_new_labels": self._min_new_labels, "ran": False,
        }
        repositories = str(getattr(self.settings, "governance_repositories", "") or "")
        if self._governance_runner is not None and repositories:
            result["governance"] = {}
            for repository in [item.strip() for item in repositories.split(",") if item.strip()]:
                try:
                    scan = self._governance_runner(repository, push=False)
                    result["governance"][repository] = {
                        "report_id": scan.get("report_id"),
                        "prs": scan.get("prs"), "findings": scan.get("findings"),
                    }
                except Exception as exc:
                    result["governance"][repository] = {"error": str(exc)[:300]}
        if new_labels < self._min_new_labels:
            self.last_attempt = result
            return result
        try:
            manifest = self._run_pipeline({})
            samples = self.store.list_sft_samples(
                status="selected",
                limit=int(getattr(self.settings, "flywheel_min_new_labels", 20)) * 40,
            )
            training = {}
            if samples and getattr(self.settings, "flywheel_auto_train", False):
                training = self._run_training_job(
                    samples, layer="preflight", activate=False
                )
            result.update({
                "ran": True, "pipeline": {
                    "dataset_version": manifest.get("dataset_version"),
                    "total": manifest.get("total"),
                },
                "training": training,
            })
        finally:
            self._last_labels = now
        self.last_attempt = result
        return result

    def set_baseline(self) -> None:
        self._last_labels = self.store.count_task_labels()
