"""실제 모델 없는 Qwen 공통 batch 계약·잘림 거부·실패 원문 보존 검사."""

import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr.adapters import create_adapter, qwen3
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio
from edge_asr.evaluation import evaluate, rescore

ROOT = Path(__file__).resolve().parents[1]


class QwenAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / "model"
        fixture = b"mock asset: no model weights"
        self.hashes = {name: hashlib.sha256(fixture).hexdigest() for name in qwen3.MODEL_FILES}
        for name in self.hashes:
            p = self.directory / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(fixture)
        hashes = patch.object(qwen3, "MODEL_FILES", self.hashes)
        hashes.start()
        self.addCleanup(hashes.stop)
        self.config = self.root / "config.toml"
        self.config.write_text(
            (ROOT / "configs/qwen3_eval.toml")
            .read_text()
            .replace("models/qwen3-20261106-01/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25", "model")
        )
        self.model = load_evaluation_config(self.config)["model"]
        self.streams = []
        self.raw, self.tokens, self.warning = "한 글 123!", ["한", " 글", " 123", "!"], ""
        owner = self

        class Stream:
            def set_option(self, name, value):
                self.language = (name, value)

            def accept_waveform(self, rate, samples):
                self.rate, self.samples = rate, list(samples)

        class Recognizer:
            def create_stream(self):
                s = Stream()
                owner.streams.append(s)
                return s

            def decode_stream(self, stream):
                if owner.warning:
                    os.write(2, owner.warning.encode())
                stream.result = types.SimpleNamespace(text=owner.raw, tokens=owner.tokens)

        self.constructor = Mock(return_value=Recognizer())
        self.sherpa = types.SimpleNamespace(
            __version__="2.0.1",
            onnxruntime_version="fixture-ort",
            OfflineRecognizer=types.SimpleNamespace(from_qwen3_asr=self.constructor),
        )
        modules = patch.dict(
            sys.modules,
            sherpa_onnx=self.sherpa,
            numpy=types.SimpleNamespace(
                float32="float32",
                frombuffer=lambda a, dtype: a,
            ),
        )
        modules.start()
        self.addCleanup(modules.stop)

    def adapter(self):
        return create_adapter(self.model, 16000, self.root)

    def audio(self, seconds=0.1):
        return Audio(array("f", [0.25] * int(16000 * seconds)), 16000, "fixture")

    def test_language_fresh_streams_128_features_and_unmodified_text(self):
        adapter = self.adapter()
        adapter.load()
        for _ in range(2):
            self.assertEqual(adapter.transcribe(self.audio()), self.raw)
        self.assertEqual(len(self.streams), 2)
        self.assertIsNot(self.streams[0], self.streams[1])
        self.assertTrue(all(s.language == ("language", "Korean") for s in self.streams))
        self.assertTrue(all(s.samples == list(self.audio().samples) for s in self.streams))
        self.assertEqual(self.constructor.call_args.kwargs["feature_dim"], 128)
        self.assertNotIn("use_itn", self.constructor.call_args.kwargs)
        self.assertIsNone(adapter.metadata["use_itn"])
        self.assertEqual(adapter.last_result_metadata["tokens"], self.tokens)
        adapter.close()
        with self.assertRaises(RuntimeError):
            adapter.transcribe(self.audio())

    def test_unknown_assets_fail_before_runtime_constructor(self):
        (self.directory / "encoder.int8.onnx").write_bytes(b"different model")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.adapter()
        self.constructor.assert_not_called()

    def test_runtime_version_mismatch_is_explicit(self):
        self.sherpa.__version__ = "9.9.9"
        with self.assertRaisesRegex(ValueError, "2.0.1"):
            self.adapter().load()
        self.constructor.assert_not_called()

    def test_context_bounds_use_frames_and_reject_without_feeding_audio(self):
        budget = qwen3.context_budget(56424, 128, 512)
        self.assertEqual((budget["feature_frames"], budget["audio_tokens_upper_bound"]), (352, 46))
        self.assertTrue(budget["fits"])
        adapter = self.adapter()
        adapter.load()
        with self.assertRaisesRegex(ValueError, "상한 초과"):
            adapter.transcribe(self.audio(60))
        self.assertFalse(self.streams)
        self.assertFalse(adapter.last_result_metadata["context_budget"]["fits"])

    def test_native_warning_and_token_cap_are_failures_with_raw_preserved(self):
        adapter = self.adapter()
        adapter.load()
        self.warning = "Result is truncated. max_new_tokens is too small\n"
        fd_identity = (os.fstat(2).st_dev, os.fstat(2).st_ino)
        with self.assertRaisesRegex(RuntimeError, "잘림"):
            adapter.transcribe(self.audio())
        self.assertEqual((os.fstat(2).st_dev, os.fstat(2).st_ino), fd_identity)
        self.assertEqual(adapter.last_result_metadata["raw_text"], self.raw)
        self.assertEqual(adapter.last_result_metadata["native_stderr"], self.warning)
        self.warning = ""
        self.tokens = ["x"] * 128
        with self.assertRaisesRegex(RuntimeError, "token 상한"):
            adapter.transcribe(self.audio())

    def manifest(self):
        p = self.root / "one.wav"
        with wave.open(str(p), "wb") as w:
            w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            w.writeframes(b"\0\0" * 1600)
        manifest = self.root / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"id": "one", "audio_path": "one.wav", "reference": "한글 123!"}) + "\n"
        )
        return manifest

    def test_common_batch_warmup_cer_and_model_free_rescore(self):
        output, code = evaluate(self.config, self.manifest(), self.root)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.streams), 4)
        info = json.loads((output / "manifest.json").read_text())
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["overall"]["cer"]["value"], 0)
        self.assertEqual(len(info["model_files"]), 6)
        self.assertIsNone(summary["unmeasured"]["first_partial_from_audio_start_s"])
        with patch("edge_asr.evaluation.create_adapter", side_effect=AssertionError("재추론 금지")):
            scored, code = rescore(output / "predictions.jsonl", self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((scored / "summary.json").read_text()), summary)

    def test_common_batch_keeps_failed_output_and_excludes_it_from_cer(self):
        self.config.write_text(
            self.config.read_text().replace("warmup_runs = 3", "warmup_runs = 0")
        )
        self.warning = "qwen3-asr: truncating audio placeholders\n"
        output, code = evaluate(self.config, self.manifest(), self.root)
        row = json.loads((output / "predictions.jsonl").read_text())
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual((code, row["status"]), (1, "failed"))
        self.assertIsNone(row["hypothesis"])
        self.assertEqual(row["adapter_result"]["raw_text"], self.raw)
        self.assertEqual(summary["overall"]["successful"], 0)
        self.assertIsNone(summary["overall"]["cer"]["value"])

    def test_config_rejects_unsupported_options_and_streaming_before_output(self):
        text = self.config.read_text()
        replacements = [
            ('language = "ko"', 'language = "auto"'),
            ('quantization = "int8"', 'quantization = "fp32"'),
            ("max_total_len = 512", "max_total_len = 4096"),
            ("max_new_tokens = 128", "max_new_tokens = true"),
            ("max_new_tokens = 128", "max_new_tokens = 0"),
            ("sample_rate_hz = 16000", "sample_rate_hz = 8000"),
            ("num_threads = 1", "num_threads = 1\nuse_itn = false"),
            ('mode = "batch"', 'mode = "paced_stream"'),
        ]
        for before, after in replacements:
            with self.subTest(after=after):
                self.config.write_text(text.replace(before, after))
                with self.assertRaises(ValueError):
                    evaluate(self.config, self.root / "missing", self.root)
        self.assertFalse((self.root / "results").exists())
        self.constructor.assert_not_called()
