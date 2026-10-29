"""모의 어댑터·임시 오디오로 모델 재사용과 독립 재채점 검증."""

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from edge_asr.adapters import create_adapter
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio
from edge_asr.evaluation import evaluate, rescore

ROOT = Path(__file__).resolve().parents[1]


class FakeAdapter:
    def __init__(self, root):
        self.model_files = {"fake": root / "weights.fixture"}
        self.loads = self.calls = self.closes = 0
        self.failure = None

    def load(self):
        self.loads += 1

    def transcribe(self, audio):
        self.calls += 1
        if self.failure:
            self.failure(self.calls)
        return "가"

    def close(self):
        self.closes += 1


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "weights.fixture").write_bytes(b"synthetic weights, not ASR")
        source = (
            (ROOT / "configs/zipformer_eval.toml")
            .read_text()
            .replace("warmup_runs = 3", "warmup_runs = 2")
        )
        self.config = self.root / "config.toml"
        self.config.write_text(source)
        for name in ("a", "b"):
            with wave.open(str(self.root / f"{name}.wav"), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 160)
        self.manifest = self.root / "manifest.jsonl"
        self.manifest.write_text(
            "\n".join(
                json.dumps(
                    {"id": name, "audio_path": f"{name}.wav", "reference": "가"}, ensure_ascii=False
                )
                for name in ("a", "b")
            )
            + "\n"
        )
        self.fake = FakeAdapter(self.root)

    def run_eval(self):
        return evaluate(
            self.config, self.manifest, self.root, adapter_factory=lambda *args: self.fake
        )

    def test_model_once_warmup_excluded_and_outputs_complete(self):
        output, code = self.run_eval()
        self.assertEqual(code, 0)
        self.assertEqual((self.fake.loads, self.fake.calls, self.fake.closes), (1, 4, 1))
        summary = json.loads((output / "summary.json").read_text())
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(summary["overall"]["successful"], 2)
        self.assertEqual(summary["overall"]["cer"]["value"], 0)
        self.assertEqual(manifest["warmup"]["successful"], 2)
        self.assertIsNone(summary["unmeasured"]["aligned_recognition_latency_s"])
        self.assertEqual(len(manifest["model_files"]["fake"]["sha256"]), 64)

    def test_missing_audio_continues_and_warms_up_first_valid_file(self):
        (self.root / "a.wav").unlink()
        output, code = self.run_eval()
        summary = json.loads((output / "summary.json").read_text())["overall"]
        self.assertEqual((code, summary["successful"], summary["failed"]), (1, 1, 1))
        self.assertEqual(summary["reference_character_coverage"], 0.5)
        self.assertEqual(self.fake.calls, 3)

    def test_expected_audio_hash_is_checked_before_inference(self):
        records = [json.loads(line) for line in self.manifest.read_text().splitlines()]
        records[0]["audio_sha256"] = "0" * 64
        self.manifest.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n"
        )
        output, code = self.run_eval()
        predictions = [
            json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()
        ]
        self.assertEqual(code, 1)
        self.assertEqual(predictions[0]["status"], "failed")
        self.assertEqual(predictions[0]["error"]["stage"], "audio")
        self.assertEqual(predictions[1]["status"], "success")
        self.assertEqual(self.fake.calls, 3)

    def test_rtf_excludes_model_load_and_warmup_time(self):
        clock = {"wall": 0.0, "cpu": 0.0}
        original_load, original_transcribe = self.fake.load, self.fake.transcribe

        def load():
            original_load()
            clock["wall"] += 10
            clock["cpu"] += 5

        def transcribe(audio):
            result = original_transcribe(audio)
            clock["wall"] += 5 if self.fake.calls <= 2 else 0.25
            clock["cpu"] += 2 if self.fake.calls <= 2 else 0.125
            return result

        with (
            patch.object(self.fake, "load", load),
            patch.object(self.fake, "transcribe", transcribe),
            patch("edge_asr.evaluation.time.perf_counter", side_effect=lambda: clock["wall"]),
            patch("edge_asr.evaluation.time.process_time", side_effect=lambda: clock["cpu"]),
        ):
            output, code = self.run_eval()
        self.assertEqual(code, 0)
        summary = json.loads((output / "summary.json").read_text())["overall"]
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["model_load_s"], 10)
        self.assertEqual(summary["total_inference_wall_s"], 0.5)
        self.assertEqual(summary["mean_process_cpu_percent"], 50)
        self.assertEqual(summary["rtf"], 25)

    def test_warmup_failure_aborts_with_pending_rows(self):
        def fail(call):
            raise RuntimeError("synthetic warmup error")

        self.fake.failure = fail
        output, code = self.run_eval()
        overall = json.loads((output / "summary.json").read_text())["overall"]
        self.assertEqual((code, overall["failed"], overall["not_attempted"]), (1, 1, 1))
        self.assertEqual(self.fake.closes, 1)

    def test_measurement_failure_continues_next_file(self):
        def fail(call):
            if call == 3:
                raise RuntimeError("synthetic decode error")

        self.fake.failure = fail
        output, code = self.run_eval()
        overall = json.loads((output / "summary.json").read_text())["overall"]
        self.assertEqual((code, overall["failed"], overall["successful"]), (1, 1, 1))
        failed = json.loads((output / "predictions.jsonl").read_text().splitlines()[0])
        self.assertGreater(
            failed["resources"]["rss_sample_count"] + failed["resources"]["rss_missing_count"], 0
        )

    def test_interrupt_keeps_success_and_partial_state(self):
        def interrupt(call):
            if call == 4:
                raise KeyboardInterrupt

        self.fake.failure = interrupt
        output, code = self.run_eval()
        self.assertEqual(code, 130)
        manifest = json.loads((output / "manifest.json").read_text())
        overall = json.loads((output / "summary.json").read_text())["overall"]
        self.assertEqual(manifest["status"], "interrupted")
        self.assertEqual((overall["successful"], overall["interrupted"]), (1, 1))
        self.assertEqual(self.fake.closes, 1)

    def test_model_load_failure_records_no_fabricated_scores(self):
        with patch.object(self.fake, "load", side_effect=ImportError("synthetic runtime missing")):
            output, code = self.run_eval()
        self.assertEqual(code, 2)
        self.assertIsNone(
            json.loads((output / "summary.json").read_text())["overall"]["cer"]["value"]
        )
        self.assertIn("runtime missing", (output / "error.txt").read_text())

    def test_rescore_without_audio_weights_or_adapter(self):
        output, _ = self.run_eval()
        before = (output / "predictions.jsonl").read_bytes()
        for name in ("a.wav", "b.wav", "weights.fixture"):
            (self.root / name).unlink()
        corrected = self.root / "corrected.jsonl"
        corrected.write_text(
            self.manifest.read_text().replace('"reference": "가"', '"reference": "나"')
        )
        with patch(
            "edge_asr.evaluation.create_adapter", side_effect=AssertionError("must not infer")
        ):
            rescored, code = rescore(
                output / "predictions.jsonl",
                self.root,
                reference_manifest=corrected,
                normalization="nfc_v1",
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads((rescored / "summary.json").read_text())["overall"]["cer"]["value"], 1
        )
        self.assertEqual((output / "predictions.jsonl").read_bytes(), before)

    def test_rescore_id_mismatch_rejected(self):
        output, _ = self.run_eval()
        self.manifest.write_text(self.manifest.read_text().replace('"id": "b"', '"id": "c"'))
        with self.assertRaises(ValueError):
            rescore(output / "predictions.jsonl", self.root, reference_manifest=self.manifest)

    def test_run_output_never_clobbers_previous_results(self):
        first, _ = self.run_eval()
        snapshot = (first / "predictions.jsonl").read_bytes()
        second, _ = self.run_eval()
        self.assertNotEqual(first, second)
        self.assertEqual((first / "predictions.jsonl").read_bytes(), snapshot)

    def test_unsupported_model_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "아직 구현하지 않은"):
            create_adapter({"family": "qwen3_asr"}, 16000, self.root)

    def test_zipformer_load_once_and_new_stream_per_file(self):
        streams = []

        class Stream:
            def accept_waveform(self, rate, samples):
                self.rate = rate

            def input_finished(self):
                self.finished = True

        class Recognizer:
            def create_stream(self):
                stream = Stream()
                stream.decoded = False
                streams.append(stream)
                return stream

            def is_ready(self, stream):
                return not stream.decoded

            def decode_stream(self, stream):
                stream.decoded = True

            def get_result(self, stream):
                return types.SimpleNamespace(text="가")

        constructor = unittest.mock.Mock(return_value=Recognizer())
        numpy = types.SimpleNamespace(
            float32="float32",
            zeros=lambda n, dtype: [0] * n,
            frombuffer=lambda samples, dtype: samples,
        )
        sherpa = types.SimpleNamespace(
            OnlineRecognizer=types.SimpleNamespace(from_transducer=constructor)
        )
        config = load_evaluation_config(self.config)
        adapter = create_adapter(config["model"], 16000, self.root)
        with patch.dict(sys.modules, numpy=numpy, sherpa_onnx=sherpa):
            adapter.load()
            audio = load_audio(self.root / "a.wav", 16000)
            self.assertEqual(adapter.transcribe(audio), "가")
            self.assertEqual(adapter.transcribe(audio), "가")
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(len(streams), 2)
        self.assertIsNot(streams[0], streams[1])
        self.assertTrue(all(stream.finished for stream in streams))
        self.assertEqual(len(adapter.padding), 10560)


class CliEvaluationTests(unittest.TestCase):
    def test_evaluation_config_check(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/edge_asr.py"),
                "check-config",
                "--evaluation",
                str(ROOT / "configs/zipformer_eval.toml"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["evaluation"]["mode"], "batch")

    def test_full_cli_with_synthetic_runtime_then_offline_score(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            modules = root / "synthetic_modules"
            modules.mkdir()
            (modules / "numpy.py").write_text(
                "float32 = 'float32'\ndef zeros(n, dtype): return [0] * n\ndef frombuffer(samples, dtype): return samples\n"
            )
            (modules / "sherpa_onnx.py").write_text("""
class Stream:
    done = False
    def accept_waveform(self, rate, samples): pass
    def input_finished(self): pass
class OnlineRecognizer:
    @staticmethod
    def from_transducer(**kwargs): return OnlineRecognizer()
    def create_stream(self): return Stream()
    def is_ready(self, stream): return not stream.done
    def decode_stream(self, stream): stream.done = True
    def get_result(self, stream): return "가"
""")
            model = root / "models/fake"
            model.mkdir(parents=True)
            for name in (
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            ):
                (model / name).write_bytes(b"synthetic fixture only")
            config = root / "config.toml"
            config.write_text(
                (ROOT / "configs/zipformer_eval.toml")
                .read_text()
                .replace("models/sherpa-onnx-streaming-zipformer-korean-2024-06-16", "models/fake")
            )
            with wave.open(str(root / "a.wav"), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 160)
            manifest = root / "list.jsonl"
            manifest.write_text(
                json.dumps(
                    {"id": "a", "audio_path": "a.wav", "reference": "가"}, ensure_ascii=False
                )
                + "\n"
            )
            prefix = [sys.executable, str(ROOT / "scripts/edge_asr.py")]
            result = subprocess.run(
                [
                    *prefix,
                    "evaluate",
                    "--root",
                    str(root),
                    "--config",
                    "config.toml",
                    "--manifest",
                    "list.jsonl",
                ],
                env={**os.environ, "PYTHONPATH": str(modules)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = next((root / "results/evaluations").iterdir())
            self.assertEqual(
                json.loads((run / "summary.json").read_text())["overall"]["cer"]["value"], 0
            )
            (root / "a.wav").unlink()
            result = subprocess.run(
                [
                    *prefix,
                    "score",
                    "--root",
                    str(root),
                    "--predictions",
                    str(run / "predictions.jsonl"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bad_manifest_returns_two_without_traceback(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/edge_asr.py"),
                "evaluate",
                "--manifest",
                "does-not-exist.jsonl",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_scoring_help_does_not_import_model_runtime(self):
        code = "from edge_asr.cli import main; import sys;\ntry: main(['score', '--help'])\nexcept SystemExit: pass\nassert 'numpy' not in sys.modules and 'sherpa_onnx' not in sys.modules"
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
