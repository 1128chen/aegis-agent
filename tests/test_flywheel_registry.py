"""Adapter version registry + gate + scheduler off-path behaviour."""
import os
import tempfile
import unittest

from aegis.flywheel.registry import adapter_gate, quality_score
from aegis.flywheel.scheduler import FlywheelScheduler
from aegis.store import TaskStore


class AdapterRegistryTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.path)
        self.store = TaskStore(self.path)

    def _save(self, score, layer="preflight"):
        return self.store.save_adapter(
            layer, {"base_model": "qwen", "adapter_sha256": "x" * 64,
                    "adapter_dir": "/tmp/x", "serving_model_id": "aegis-preflight",
                    "metrics": {"val_loss": 0.5}, "status": "staging"},
            score=score,
        )

    def test_version_chain_and_activation_exclusivity(self):
        self._save(0.6)
        second = self._save(0.7)
        third = self._save(0.8)
        versions = [item["version"] for item in self.store.list_adapters("preflight")]
        self.assertEqual([3, 2, 1], versions)
        self.assertIsNone(self.store.get_active_adapter("preflight"))
        self.assertTrue(self.store.activate_adapter("preflight", second["version"]))
        active = self.store.get_active_adapter("preflight")
        self.assertEqual(2, active["version"])
        # activating a newer version deactivates the previous one
        self.assertTrue(self.store.activate_adapter("preflight", third["version"]))
        active = self.store.get_active_adapter("preflight")
        self.assertEqual(3, active["version"])
        others = [item["active"] for item in self.store.list_adapters("preflight")]
        self.assertEqual(1, sum(bool(item) for item in others))

    def test_gate_blocks_regression(self):
        self._save(0.8)  # v1 becomes baseline once active
        self.store.activate_adapter("preflight", 1)
        self._save(0.5)  # v2 regression
        gate = adapter_gate(self.store, "preflight", 2)
        self.assertTrue(gate["present"])
        self.assertFalse(gate["pass"])
        self.assertEqual(0.8, gate["baseline_score"])
        self.assertEqual(0.5, gate["candidate_score"])
        good = adapter_gate(self.store, "preflight", 1)
        self.assertTrue(good["pass"])
        missing = adapter_gate(self.store, "preflight", 42)
        self.assertFalse(missing["present"])

    def test_quality_score(self):
        self.assertEqual(1.0, quality_score({"val_loss": 0.0}))
        self.assertEqual(0.0, quality_score({"val_loss": None}))


class SchedulerOffPathTests(unittest.TestCase):
    def test_tick_is_noop_under_min_new_labels(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        store = TaskStore(path)

        class SettingsStub:
            continuous_eval_seconds = 10
            flywheel_auto_train = False
            flywheel_min_new_labels = 20

        scheduler = FlywheelScheduler(store, SettingsStub(), None, None)
        result = scheduler.tick()
        self.assertFalse(result["ran"])
        self.assertLess(result["new_labels_since_last"], result["min_new_labels"])


if __name__ == "__main__":
    unittest.main()
