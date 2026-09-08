"""LoRA trainer/serving pure helpers (no torch import required)."""
import os
import tempfile
import unittest

from aegis.flywheel.registry import directory_sha256, quality_score
from aegis.flywheel.serve import _render_prompt, resolve_base_model
from aegis.flywheel.train import (
    default_hyperparams, label_json, prompt_text,
)


class TrainHelperTests(unittest.TestCase):
    def test_default_hyperparams_read_env(self):
        hyper = default_hyperparams({
            "AEGIS_FTW_EPOCHS": "2", "AEGIS_FTW_LR": "1e-3",
            "AEGIS_FTW_BATCH": "4", "AEGIS_FTW_R": "4",
        })
        self.assertEqual(2, hyper["epochs"])
        self.assertEqual(1e-3, hyper["learning_rate"])
        self.assertEqual(4, hyper["batch_size"])
        self.assertEqual(4, hyper["lora_r"])
        self.assertGreaterEqual(hyper["max_seq_len"], 128)

    def test_classify_formatting(self):
        row = {
            "kind": "classify",
            "payload": {
                "input": {"path": "a.py", "line": 1, "code": "eval(x)"},
                "response": {"keep": True, "risk_class": "llm-misuse",
                             "severity": "critical"},
            },
        }
        text = prompt_text(row)
        self.assertIn("risk preflight", text)
        self.assertIn("llm-misuse", text)
        self.assertIn('"code": "eval(x)"', text)
        label = label_json(row)
        self.assertIn('"keep": true', label)
        self.assertIn("critical", label)

    def test_instruct_formatting(self):
        row = {
            "kind": "instruct",
            "payload": {
                "prompt": {"system": "you are sec", "user": "diff context"},
                "response": {"action": "final", "findings": []},
            },
        }
        self.assertIn("you are sec", prompt_text(row))
        self.assertIn("diff context", prompt_text(row))
        self.assertIn("final", label_json(row))

    def test_preference_formatting(self):
        row = {
            "kind": "preference",
            "payload": {
                "input": {"instruction": "choose the better finding"},
                "chosen": {"title": "good"},
                "rejected": {"title": "bad"},
            },
        }
        self.assertIn("good", prompt_text(row))
        self.assertIn("bad", prompt_text(row))


class ServeHelperTests(unittest.TestCase):
    def test_render_prompt(self):
        rendered = _render_prompt([
            {"role": "system", "content": "sys-a"},
            {"role": "user", "content": "usr-b"},
        ])
        self.assertEqual("sys-a\n\nusr-b", rendered)
        self.assertIn("only-user", _render_prompt([{"role": "user", "content": "only-user"}]))

    def test_resolve_base_model_from_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "metrics.json"), "w", encoding="utf-8") as handle:
                handle.write('{"base_model": "Qwen/Qwen2.5-Coder-1.5B"}')
            self.assertEqual(
                "Qwen/Qwen2.5-Coder-1.5B", resolve_base_model(tmp, fallback="x")
            )
        self.assertEqual(
            "Qwen/Qwen2.5-Coder-0.5B-Instruct",
            resolve_base_model("/does/not/exist"),
        )

    def test_directory_sha256_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "b.txt"), "w") as handle:
                handle.write("beta")
            with open(os.path.join(tmp, "a.txt"), "w") as handle:
                handle.write("alpha")
            first = directory_sha256(tmp)
            with open(os.path.join(tmp, "b.txt"), "w") as handle:
                handle.write("beta")  # same content, later write
            self.assertEqual(first, directory_sha256(tmp))


class RegistryHelperTests(unittest.TestCase):
    def test_quality_score(self):
        self.assertEqual(1.0, quality_score({"val_loss": 0.0}))
        self.assertAlmostEqual(0.5, quality_score({"val_loss": 1.0}))
        self.assertEqual(0.0, quality_score({"val_loss": None}))


if __name__ == "__main__":
    unittest.main()
