"""독립 입력·실제 유한 큐·강제 손실·부분 가설·중단·오프라인 재채점 계약."""

import copy
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio
from edge_asr.evaluation import evaluate, rescore
from edge_asr.scoring import read_predictions, score_records
from edge_asr.streaming import backlog_series, run_paced

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = {
    "chunk_ms": 10,
    "queue_capacity_chunks": 5,
    "drop_policy": "drop_newest",
    "drain_timeout_s": 1.0,
}


class FakeStreamingAdapter:
    def __init__(self, root=None, *, partial=True, delay=0.0, failure=None):
        self.model_files = {"fake": root / "weights.fixture"} if root else {}
        self.loads = self.closes = 0
        self.streams = []
        self.partial, self.delay, self.failure = partial, delay, failure

    def load(self):
        self.loads += 1

    def close(self):
        self.closes += 1

    def start_stream(self):
        stream = {"frames": 0}
        self.streams.append(stream)
        return stream

    def accept_chunk(self, stream, samples):
        if self.failure:
            raise self.failure("synthetic inference failure")
        until = time.perf_counter() + self.delay
        while time.perf_counter() < until:
            pass  # 부모 GIL을 점유해도 독립 입력 프로세스는 실제 시간에 따라 진행해야 한다.
        stream["frames"] += len(samples)
        return "가" if self.partial else ""

    def finish_stream(self, stream):
        return "가"


class QueueMetricTests(unittest.TestCase):
    def test_backlog_excludes_inflight_chunk_and_tracks_event_peaks(self):
        produced = [
            {"type": "enqueue", "index": i, "start": i * 100, "end": (i + 1) * 100, "at_s": at}
            for i, at in enumerate((0.1, 0.2, 0.3))
        ]
        produced.append({"type": "drop", "index": 3, "start": 300, "end": 400, "at_s": 0.4})
        consumed = [{"index": i, "at_s": at} for i, at in enumerate((0.11, 0.45, 0.46))]
        samples, stats = backlog_series(produced, consumed, 1000, 0.5, 0.4)
        self.assertAlmostEqual(stats["event_max_audio_s"], 0.2)
        self.assertEqual(stats["event_max_chunks"], 2)
        self.assertAlmostEqual(stats["event_max_oldest_wait_s"], 0.25)
        self.assertEqual(stats["end_audio_s"], 0)
        self.assertEqual(stats["at_audio_end_s"], 0.2)
        self.assertEqual(next(s["queued_audio_s"] for s in samples if s["at_s"] == 0.2), 0.1)

    def test_partial_tail_and_no_partial_before_flush(self):
        with tempfile.TemporaryDirectory() as temp:
            stats = {}
            adapter = FakeStreamingAdapter(partial=False)
            audio = Audio(array("f", [0.0] * 25), 1000, "fixture")
            text = run_paced(adapter, audio, SETTINGS, Path(temp) / "events", stats)
            self.assertEqual(text, "가")
            self.assertTrue(stats["complete"])
            self.assertEqual(stats["planned_chunks"], 3)
            self.assertEqual(adapter.streams[0]["frames"], 25)
            self.assertIsNone(stats["first_partial_from_audio_start_s"])
            self.assertEqual(stats["no_partial_reason"], "no_nonempty_partial_before_flush")
            self.assertGreaterEqual(stats["replay_elapsed_s"], 0.025)
            self.assertLess(stats["processing_s"], stats["replay_elapsed_s"])
            self.assertEqual(stats["dropped_chunks"], 0)

    def test_overload_drops_new_chunks_without_slowing_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            stats = {}
            audio = Audio(array("f", [0.0] * 121), 1000, "fixture")
            directory = Path(temp) / "events"
            run_paced(
                FakeStreamingAdapter(delay=0.15),
                audio,
                {**SETTINGS, "queue_capacity_chunks": 1},
                directory,
                stats,
            )
            self.assertGreater(stats["dropped_chunks"], 0)
            self.assertEqual(stats["processed_chunks"] + stats["dropped_chunks"], 13)
            self.assertEqual(stats["backlog"]["event_max_chunks"], 1)
            produced = [
                json.loads(line) for line in (directory / "producer.jsonl").read_text().splitlines()
            ]
            drops = [e for e in produced if e["type"] == "drop"]
            self.assertAlmostEqual(
                stats["dropped_audio_s"], sum(e["end"] - e["start"] for e in drops) / 1000
            )
            self.assertEqual(
                [e for e in produced if e["type"] in {"enqueue", "drop"}][-1]["end"], 121
            )

    def test_error_and_interrupt_cleanup_child_and_keep_partial_events(self):
        before = {p.pid for p in multiprocessing.active_children()}
        for error in (RuntimeError, KeyboardInterrupt):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp:
                stats = {}
                directory = Path(temp) / "events"
                with self.assertRaises(error):
                    run_paced(
                        FakeStreamingAdapter(failure=error),
                        Audio(array("f", [0.0] * 100), 1000, "fixture"),
                        SETTINGS,
                        directory,
                        stats,
                    )
                self.assertFalse(stats["complete"])
                self.assertTrue((directory / "producer.jsonl").exists())
                self.assertIn('"dequeue"', (directory / "decoder.jsonl").read_text())
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)


class StreamingEvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "weights.fixture").write_bytes(b"synthetic")
        self.config = self.root / "config.toml"
        self.config.write_text(
            (ROOT / "configs/zipformer_paced.toml")
            .read_text()
            .replace("chunk_ms = 100", "chunk_ms = 10")
        )
        with wave.open(str(self.root / "audio.wav"), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 640)
        self.manifest = self.root / "manifest.jsonl"
        self.manifest.write_text(
            json.dumps({"id": "a", "audio_path": "audio.wav", "reference": "가"}) + "\n"
        )
        self.adapter = FakeStreamingAdapter(self.root)

    def run_eval(self):
        return evaluate(
            self.config, self.manifest, self.root, adapter_factory=lambda *args: self.adapter
        )

    def test_one_load_chunk_warmup_fresh_stream_and_offline_rescore(self):
        output, code = self.run_eval()
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.adapter.loads, self.adapter.closes, len(self.adapter.streams)), (1, 1, 4)
        )
        predictions = read_predictions(output / "predictions.jsonl")
        stats = predictions[0]["streaming"]
        self.assertEqual(stats["captured_chunks"], 4)
        self.assertEqual(stats["backlog"]["end_audio_s"], 0)
        self.assertIsNotNone(stats["first_partial_from_audio_start_s"])
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["mode"], "paced_stream")
        self.assertEqual(summary["overall"]["cer"]["value"], 0)
        self.assertGreaterEqual(summary["overall"]["streaming"]["replay_elapsed_ratio"], 1)
        self.assertNotIn("first_partial_from_audio_start_s", summary["unmeasured"])
        self.assertIsNone(summary["unmeasured"]["aligned_recognition_latency_s"])
        (self.root / "audio.wav").unlink()
        (self.root / "weights.fixture").unlink()
        shutil.rmtree(output / "events")
        original = (output / "predictions.jsonl").read_bytes()
        with patch(
            "edge_asr.evaluation.create_adapter", side_effect=AssertionError("must not infer")
        ):
            rescored, code = rescore(output / "predictions.jsonl", self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((rescored / "summary.json").read_text()), summary)
        self.assertEqual((output / "predictions.jsonl").read_bytes(), original)

    def test_failed_capture_is_not_reported_as_zero_drops_or_zero_latency(self):
        self.config.write_text(
            self.config.read_text().replace("warmup_runs = 3", "warmup_runs = 0")
        )
        self.adapter.failure = RuntimeError
        output, code = self.run_eval()
        self.assertEqual(code, 1)
        summary = json.loads((output / "summary.json").read_text())["overall"]
        self.assertEqual(summary["failed"], 1)
        self.assertIsNone(summary["streaming"]["dropped_chunks"])
        self.assertIsNone(summary["streaming"]["first_partial_from_audio_start_s"]["p50"])
        self.assertEqual(summary["streaming"]["included_files"], 0)

    def test_bad_telemetry_and_mixed_modes_are_rejected(self):
        output, _ = self.run_eval()
        row = read_predictions(output / "predictions.jsonl")[0]
        broken = copy.deepcopy(row)
        broken["streaming"]["finalization_from_audio_end_s"] = float("nan")
        path = self.root / "invalid.jsonl"
        path.write_text(json.dumps(broken) + "\n")
        with self.assertRaises(ValueError):
            read_predictions(path)
        mixed = {**row, "id": "b", "mode": "batch"}
        path.write_text(json.dumps(row) + "\n" + json.dumps(mixed) + "\n")
        with self.assertRaisesRegex(ValueError, "섞지"):
            read_predictions(path)
        with self.assertRaises(ValueError):
            score_records([row, mixed], "ko_cer_v1")

    def test_interrupt_saves_incomplete_row_and_returns_130(self):
        self.config.write_text(
            self.config.read_text().replace("warmup_runs = 3", "warmup_runs = 0")
        )
        self.adapter.failure = KeyboardInterrupt
        output, code = self.run_eval()
        self.assertEqual(code, 130)
        self.assertEqual(read_predictions(output / "predictions.jsonl")[0]["status"], "interrupted")
        self.assertEqual(
            json.loads((output / "manifest.json").read_text())["status"], "interrupted"
        )
        self.assertFalse(json.loads((output / "summary.json").read_text())["overall"]["complete"])
        self.assertEqual(self.adapter.closes, 1)

    def test_config_policy_bounds_and_unused_streaming_table(self):
        text = self.config.read_text()
        for before, after in (
            ("chunk_ms = 10", "chunk_ms = true"),
            ("queue_capacity_chunks = 5", "queue_capacity_chunks = 0"),
            ('drop_policy = "drop_newest"', 'drop_policy = "block"'),
            ("drain_timeout_s = 10.0", "drain_timeout_s = nan"),
            ('mode = "paced_stream"', 'mode = "batch"'),
        ):
            with self.subTest(after=after):
                self.config.write_text(text.replace(before, after))
                with self.assertRaises(ValueError):
                    load_evaluation_config(self.config)

    def test_real_cli_spawn_with_synthetic_runtime(self):
        modules = self.root / "modules"
        modules.mkdir()
        (modules / "numpy.py").write_text(
            "float32='float32'\ndef zeros(n,dtype): return [0]*n\ndef frombuffer(samples,dtype): return samples\n"
        )
        (modules / "sherpa_onnx.py").write_text("""
class Stream:
    ready = False
    def accept_waveform(self, rate, samples): self.ready = True
    def input_finished(self): pass
class OnlineRecognizer:
    @staticmethod
    def from_transducer(**kwargs): return OnlineRecognizer()
    def create_stream(self): return Stream()
    def is_ready(self, stream): return stream.ready
    def decode_stream(self, stream): stream.ready = False
    def get_result(self, stream): return '가'
""")
        model = self.root / "models/sherpa-onnx-streaming-zipformer-korean-2024-06-16"
        model.mkdir(parents=True)
        for name in (
            "tokens.txt",
            "encoder-epoch-99-avg-1.int8.onnx",
            "decoder-epoch-99-avg-1.onnx",
            "joiner-epoch-99-avg-1.int8.onnx",
        ):
            (model / name).write_bytes(b"synthetic")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/edge_asr.py"),
                "evaluate",
                "--root",
                str(self.root),
                "--config",
                str(self.config),
                "--manifest",
                str(self.manifest),
            ],
            env={**os.environ, "PYTHONPATH": str(modules)},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = next((self.root / "results/evaluations").iterdir())
        self.assertTrue(read_predictions(output / "predictions.jsonl")[0]["streaming"]["complete"])
