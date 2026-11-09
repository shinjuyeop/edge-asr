"""Qwen smoke의 계측·실패 보존을 모의 런타임으로 검사한다. 실제 ARM64 추론 아님."""

import json
import sys
import tempfile
import types
import unittest
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr.dataset import Audio
from edge_asr.tools.optimization import qwen3


class QwenProbeTests(unittest.TestCase):
    def run_probe(self, root, *, failure=False):
        stream = Mock(
            result=types.SimpleNamespace(text="한 글 123!", tokens=["한", " 글", " 123", "!"])
        )
        recognizer = Mock()
        recognizer.create_stream.return_value = stream
        constructor = Mock(return_value=recognizer)
        if failure:
            constructor.side_effect = RuntimeError("synthetic allocation failure")
        modules = {
            "sherpa_onnx": types.SimpleNamespace(
                OfflineRecognizer=types.SimpleNamespace(from_qwen3_asr=constructor)
            ),
            "numpy": types.SimpleNamespace(float32="float32", frombuffer=lambda a, dtype: a),
        }
        audio = Audio(array("f", [0.25] * 160), 16000, "fixture")
        with (
            patch.dict(sys.modules, modules),
            patch.object(qwen3, "limits_verified", return_value=True),
            patch.object(qwen3, "environment", return_value={"fixture": True}),
            patch.object(qwen3, "digest", return_value="fixture-sha"),
            patch.object(qwen3, "load_audio", return_value=audio),
            patch.object(qwen3.subprocess, "check_output", return_value="fixture-commit"),
        ):
            code = qwen3.probe(root, root / "model", root / "audio.wav", root)
        return code, json.loads((root / "worker.json").read_text()), constructor, stream

    def test_separate_load_and_first_inference_preserve_language_and_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code, record, constructor, stream = self.run_probe(root)
            self.assertEqual((code, record["status"]), (0, "completed"))
            self.assertEqual(record["hypothesis"], "한 글 123!")
            self.assertEqual(record["warmup_runs"], 0)
            self.assertIsNone(record["cer"])
            stream.set_option.assert_called_once_with("language", "Korean")
            self.assertEqual(constructor.call_args.kwargs["feature_dim"], 128)
            self.assertNotIn("use_itn", constructor.call_args.kwargs)
            for key in ("model_load", "first_inference"):
                self.assertGreaterEqual(record[key]["resources"]["rss_sample_count"], 1)
            before = (root / "worker.json").read_bytes()
            with self.assertRaises(FileExistsError):
                qwen3.probe(root, root, root, root)
            self.assertEqual((root / "worker.json").read_bytes(), before)

    def test_load_failure_keeps_observations_and_does_not_transcribe(self):
        with tempfile.TemporaryDirectory() as temp:
            code, record, _, stream = self.run_probe(Path(temp), failure=True)
        self.assertEqual((code, record["status"]), (1, "failed"))
        self.assertIn("allocation failure", record["error"])
        self.assertIsNotNone(record["model_load"])
        self.assertIsNone(record["first_inference"])
        self.assertIsNone(record["hypothesis"])
        stream.accept_waveform.assert_not_called()

    def test_missing_external_limit_rejects_before_creating_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(qwen3, "limits_verified", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "4GiB"):
                    qwen3.probe(root, root, root, root)
            self.assertFalse((root / "worker.json").exists())
