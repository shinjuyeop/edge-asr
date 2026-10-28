"""실제 모델·런타임 없이 SenseVoice Offline 계약과 공통 평가 호환성을 검사한다."""

from array import array
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import wave

from edge_asr.adapters import create_adapter
from edge_asr.adapters.sensevoice import SenseVoiceAdapter
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio
from edge_asr.evaluation import evaluate, rescore

ROOT = Path(__file__).resolve().parents[1]
RAW = " <|ko|><|NEUTRAL|><|Speech|><|woitn|>한 글, 입니다! "


class SenseVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "models/fake"
        self.directory.mkdir(parents=True)
        for name in ("model.int8.onnx", "tokens.txt"):
            (self.directory / name).write_bytes(b"synthetic fixture, not model weights")
        self.config = self.root / "sensevoice.toml"
        self.config.write_text((ROOT / "configs/sensevoice_eval.toml").read_text().replace(
            "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17", "models/fake"))
        self.model = load_evaluation_config(self.config)["model"]
        self.streams = []
        owner = self

        class Stream:
            def accept_waveform(self, rate, samples):
                self.rate, self.samples = rate, list(samples)

        class Recognizer:
            def create_stream(self):
                stream = Stream()
                owner.streams.append(stream)
                return stream

            def decode_stream(self, stream):
                stream.result = types.SimpleNamespace(text=owner.raw)

        self.raw = RAW
        self.constructor = Mock(return_value=Recognizer())
        self.numpy = types.SimpleNamespace(float32="float32", frombuffer=lambda samples, dtype: samples,
                                          zeros=Mock(side_effect=AssertionError("SenseVoice에 Zipformer padding 금지")))
        self.sherpa = types.SimpleNamespace(onnxruntime_version="fixture-native-ort",
                                            OfflineRecognizer=types.SimpleNamespace(from_sense_voice=self.constructor))
        self.modules = patch.dict(sys.modules, numpy=self.numpy, sherpa_onnx=self.sherpa)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def adapter(self, **overrides):
        return create_adapter({**self.model, **overrides}, 16000, self.root)

    def test_factory_options_one_load_fresh_states_and_no_padding(self):
        adapter = self.adapter()
        self.assertIsInstance(adapter, SenseVoiceAdapter)
        adapter.load()
        audio = Audio(array("f", [0.0, 0.5, -0.5]), 16000, "fixture")
        self.assertEqual(adapter.transcribe(audio), "한 글, 입니다!")
        adapter.transcribe(audio)
        self.assertEqual(self.constructor.call_count, 1)
        self.assertEqual(len(self.streams), 2)
        self.assertIsNot(self.streams[0], self.streams[1])
        self.assertTrue(all(s.rate == 16000 and s.samples == list(audio.samples) for s in self.streams))
        kwargs = self.constructor.call_args.kwargs
        self.assertEqual((kwargs["language"], kwargs["use_itn"], kwargs["provider"], kwargs["feature_dim"]),
                         ("ko", False, "cpu", 80))
        self.assertEqual(kwargs["model"], str(self.directory / "model.int8.onnx"))
        self.numpy.zeros.assert_not_called()
        self.assertNotIn("tail_padding_s", self.model)
        with self.assertRaises(RuntimeError):
            adapter.load()
        adapter.close()
        with self.assertRaises(RuntimeError):
            adapter.transcribe(audio)

    def test_itn_and_language_are_forwarded_without_changing_cer_policy(self):
        adapter = self.adapter(language="auto", use_itn=True)
        adapter.load()
        self.assertEqual(self.constructor.call_args.kwargs["language"], "auto")
        self.assertIs(self.constructor.call_args.kwargs["use_itn"], True)

    def test_control_tags_and_raw_text_are_separate(self):
        adapter = self.adapter()
        adapter.load()
        self.raw = "<|ko|><|FUTURE_EVENT|> <주의>  한 글!"
        text = adapter.transcribe(Audio(array("f", [0]), 16000, "fixture"))
        self.assertEqual(text, "<주의>  한 글!")
        self.assertEqual(adapter.last_result_metadata["raw_text"], self.raw)
        self.assertEqual(adapter.last_result_metadata["special_tags"], ["<|ko|>", "<|FUTURE_EVENT|>"])
        self.assertEqual(adapter.metadata["output_policy"], "sensevoice_control_tokens_v1")
        self.raw = "<|ko|><|nospeech|>"
        self.assertEqual(adapter.transcribe(Audio(array("f", [0]), 16000, "fixture")), "")

    def test_missing_and_empty_assets_fail_before_native_constructor(self):
        (self.directory / "model.int8.onnx").unlink()
        with self.assertRaises(FileNotFoundError):
            self.adapter().load()
        (self.directory / "model.int8.onnx").touch()
        with self.assertRaises(ValueError):
            self.adapter().load()
        self.constructor.assert_not_called()

    def test_invalid_audio_and_nonstring_result(self):
        adapter = self.adapter()
        adapter.load()
        for audio in (Audio(array("f"), 16000, "fixture"), Audio(array("f", [0]), 8000, "fixture")):
            with self.assertRaises(ValueError):
                adapter.transcribe(audio)
        with self.assertRaises(TypeError):
            adapter.transcribe(Audio(array("h", [0]), 16000, "fixture"))
        self.raw = None
        with self.assertRaises(TypeError):
            adapter.transcribe(Audio(array("f", [0]), 16000, "fixture"))
        self.assertIsNone(adapter.last_result_metadata)

    def make_manifest(self):
        for name in ("a", "b"):
            with wave.open(str(self.root / (name + ".wav")), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 160)
        path = self.root / "manifest.jsonl"
        path.write_text("".join(json.dumps({"id": name, "audio_path": name + ".wav", "reference": "한글입니다"}, ensure_ascii=False) + "\n" for name in ("a", "b")))
        return path

    def test_common_evaluation_metadata_cer_and_model_free_rescore(self):
        manifest = self.make_manifest()
        output, code = evaluate(self.config, manifest, self.root)
        self.assertEqual(code, 0)
        self.assertEqual(self.constructor.call_count, 1)
        self.assertEqual(len(self.streams), 5)  # 워밍업 3 + 발화 2, 모두 새 상태
        info = json.loads((output / "manifest.json").read_text())
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["overall"]["cer"]["value"], 0)
        self.assertIsNone(summary["unmeasured"]["first_partial_from_audio_start_s"])
        self.assertIn("offline_refine_confirmation_latency_s", info["unmeasured_reasons"])
        self.assertEqual(info["model_metadata"]["adapter"]["quantization"], "int8")
        self.assertIsNone(info["model_metadata"]["adapter"]["quantization_method"])
        self.assertTrue(info["model_metadata"]["adapter"]["quantization_method_reason"])
        self.assertIsNone(info["model_metadata"]["adapter"]["tail_padding_s"])
        self.assertGreaterEqual(info["model_load_s"], 0)
        self.assertGreaterEqual(info["model_load_resources"]["rss_sample_count"], 1)
        predictions = [json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()]
        self.assertEqual(predictions[0]["adapter_result"]["raw_text"], RAW)
        self.assertEqual(predictions[0]["hypothesis"], "한 글, 입니다!")
        self.assertGreaterEqual(predictions[0]["resources"]["rss_sample_count"], 1)
        self.assertEqual(json.loads((output / "environment.json").read_text())["runtime"]["sherpa_native_onnxruntime_version"], "fixture-native-ort")
        before = (output / "predictions.jsonl").read_bytes()
        for path in (*self.directory.iterdir(), self.root / "a.wav", self.root / "b.wav"):
            path.unlink()
        with patch("edge_asr.evaluation.create_adapter", side_effect=AssertionError("재추론 금지")):
            scored, code = rescore(output / "predictions.jsonl", self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((scored / "summary.json").read_text()), summary)
        self.assertEqual((output / "predictions.jsonl").read_bytes(), before)

    def test_load_failure_retains_loading_observation_and_pending_rows(self):
        self.constructor.side_effect = RuntimeError("synthetic unsupported model")
        output, code = evaluate(self.config, self.make_manifest(), self.root)
        info = json.loads((output / "manifest.json").read_text())
        row = json.loads((output / "predictions.jsonl").read_text().splitlines()[0])
        self.assertEqual((code, info["status"]), (2, "setup_failed"))
        self.assertIsNotNone(info["model_load_resources"])
        self.assertIsNotNone(info["model_load_s"])
        self.assertIsNone(row["resources"])
        self.assertEqual(row["status"], "not_attempted")

    def test_missing_audio_fails_one_row_without_adding_padding_or_hiding_failure(self):
        path = self.make_manifest()
        (self.root / "a.wav").unlink()
        output, code = evaluate(self.config, path, self.root)
        summary = json.loads((output / "summary.json").read_text())["overall"]
        self.assertEqual((code, summary["failed"], summary["successful"]), (1, 1, 1))
        self.assertEqual(summary["reference_character_coverage"], 0.5)

    def test_bad_model_settings_and_streaming_are_rejected_before_output(self):
        text = self.config.read_text()
        cases = [("language = \"ko\"", "language = \"invalid\""),
                 ("language = \"ko\"", ""), ("use_itn = false", "use_itn = 1"),
                 ("use_itn = false", ""), ("num_threads = 1", "num_threads = 0"),
                 ('model_file = "model.int8.onnx"', 'model_file = " "'),
                 ('quantization = "int8"', 'quantization = "fp32"'),
                 ("sample_rate_hz = 16000", "sample_rate_hz = 8000"),
                 ('family = "sensevoice"', 'family = "qwen3_asr"'),
                 ("num_threads = 1", "num_threads = 1\ntail_padding_s = 0.66")]
        for before, after in cases:
            with self.subTest(after=after):
                self.config.write_text(text.replace(before, after))
                with self.assertRaises(ValueError):
                    load_evaluation_config(self.config)
        self.config.write_text(text.replace('mode = "batch"', 'mode = "paced_stream"'))
        with self.assertRaisesRegex(ValueError, "sensevoice.*batch"):
            evaluate(self.config, self.root / "missing.jsonl", self.root)
        self.assertFalse((self.root / "results").exists())
        self.constructor.assert_not_called()

    def test_independent_cli_with_mock_runtime_and_offline_score(self):
        manifest = self.make_manifest()
        modules = self.root / "modules"
        modules.mkdir()
        (modules / "numpy.py").write_text("float32='float32'\ndef frombuffer(samples,dtype): return samples\n")
        (modules / "sherpa_onnx.py").write_text('''
from types import SimpleNamespace
onnxruntime_version = 'fixture-native-ort'
class Stream:
    def accept_waveform(self, rate, samples): assert rate == 16000 and len(samples) == 160
class OfflineRecognizer:
    @staticmethod
    def from_sense_voice(**kw):
        assert kw['language'] == 'ko' and kw['use_itn'] is False
        return OfflineRecognizer()
    def create_stream(self): return Stream()
    def decode_stream(self, stream): stream.result = SimpleNamespace(text='<|ko|>한글입니다')
''')
        command = [sys.executable, str(ROOT / "scripts/edge_asr.py")]
        env = {**os.environ, "PYTHONPATH": str(modules)}
        result = subprocess.run([*command, "evaluate", "--root", str(self.root), "--config", str(self.config),
                                 "--manifest", str(manifest)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = next((self.root / "results/evaluations").iterdir())
        result = subprocess.run([*command, "score", "--root", str(self.root), "--predictions", str(output / "predictions.jsonl")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
