"""실제 ASR 없이 유한 Refine 큐·IPC·좌표·손실·재채점·프로세스 회수를 검사한다."""

import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from array import array
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.concurrent_probe import ConcurrentReplay, RefineChannel, float_digest
from edge_asr.dataset import Audio, write_wav
from edge_asr.streaming import read_events, run_paced
from edge_asr.tools.reports.concurrent import resource_summary, summarize

ROOT = Path(__file__).resolve().parents[1]


class OfflineFixture:
    model_files = {}
    metadata = {"fixture": True}
    last_result_metadata = {"raw_text": "가", "special_tags": []}

    def load(self):
        pass

    def close(self):
        pass

    def transcribe(self, audio):
        if audio.samples[0] < 0:
            raise RuntimeError("fixture native failure")
        time.sleep(0.06)
        return "가"


def offline_factory(*args):
    return OfflineFixture()


class StreamingFixture:
    def __init__(self, delay=0):
        self.delay = delay

    def start_stream(self):
        return {}

    def accept_chunk(self, stream, samples):
        if self.delay:
            time.sleep(self.delay)
        return "가"

    def finish_stream(self, stream):
        return "가"


class ConcurrentCoreTests(unittest.TestCase):
    def test_window_boundaries_cross_chunks_and_sources_without_loss_or_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = array("f", (i / 200000 for i in range(85017)))
            sources = [
                {"id": "a", "reference": "가", "audio_sha256": "x", "start": 0, "end": 81013},
                {"id": "b", "reference": "가", "audio_sha256": "y", "start": 81013, "end": 85017},
            ]
            received = []

            def submit(job):
                received.append(job)
                return "enqueue", time.perf_counter_ns()

            with (root / "zip").open("x") as z, (root / "jobs").open("x") as j:
                replay = ConcurrentReplay(StreamingFixture(), sources, {}, submit, z, j)
                replay.start_stream()
                for start in range(0, len(samples), 1600):
                    replay.accept_at(
                        replay,
                        samples[start : start + 1600],
                        start,
                        min(start + 1600, len(samples)),
                    )
                self.assertEqual(replay.finish_stream(replay), "가 가")
            self.assertEqual(
                [(j["start"], j["end"]) for j in received],
                [(0, 80000), (80000, 81013), (81013, 85017)],
            )
            self.assertEqual(array("f", (x for j in received for x in j["samples"])), samples)
            for job in received:
                self.assertEqual(job["payload_float32_sha256"], float_digest(job["samples"]))

    def test_overload_capture_is_not_compressed_into_false_source_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = array("f", [0] * 3200)
            source = {
                "id": "a",
                "reference": "가",
                "audio_sha256": "x",
                "start": 0,
                "end": len(samples),
            }
            telemetry = {}
            with (root / "zip").open("x") as z, (root / "jobs").open("x") as j:
                replay = ConcurrentReplay(StreamingFixture(0.12), [source], telemetry, None, z, j)
                with self.assertRaises(RuntimeError):
                    run_paced(
                        replay,
                        Audio(samples, 16000, "x"),
                        {
                            "chunk_ms": 10,
                            "queue_capacity_chunks": 1,
                            "drop_policy": "drop_newest",
                            "drain_timeout_s": 1,
                        },
                        root / "capture",
                        telemetry,
                        chunk_handler=replay.accept_at,
                    )
            self.assertGreater(telemetry["dropped_chunks"], 0)
            self.assertFalse(telemetry["complete"])

    def test_real_refine_queue_is_bounded_preserves_pcm_and_reaps_child(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = Audio(array("f", [0] * 160), 16000, "x")
            channel = RefineChannel(root, {}, audio, root, factory=offline_factory)
            accepted = []
            drops = 0
            try:
                self.assertEqual(channel.start()["warmup_runs"], 3)
                for i in range(20):
                    samples = array("f", [i / 20] * 160)
                    job = {
                        "index": i,
                        "source_index": 0,
                        "source_id": "a",
                        "start": i * 160,
                        "end": (i + 1) * 160,
                        "samples": samples,
                        "payload_frames": 160,
                        "payload_float32_sha256": float_digest(samples),
                        "submitted_ns": time.perf_counter_ns(),
                    }
                    kind, _ = channel.submit(job)
                    if kind == "enqueue":
                        accepted.append(i)
                    else:
                        drops += 1
                done = channel.finish()
                self.assertEqual(done["jobs"], len(accepted))
            finally:
                channel.close()
            rows = read_events(root / "refine-results.jsonl")
            self.assertEqual([r["index"] for r in rows], accepted)
            self.assertGreater(drops, 0)
            self.assertTrue(all(r["status"] == "success" for r in rows))
            self.assertFalse(channel.process.is_alive())
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_native_failure_is_recorded_and_cleanup_does_not_leave_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = Audio(array("f", [0] * 160), 16000, "x")
            channel = RefineChannel(root, {}, audio, root, factory=offline_factory)
            try:
                channel.start()
                samples = array("f", [-0.1] * 160)
                channel.submit(
                    {
                        "index": 0,
                        "source_index": 0,
                        "source_id": "a",
                        "start": 0,
                        "end": 160,
                        "samples": samples,
                        "payload_frames": 160,
                        "payload_float32_sha256": float_digest(samples),
                        "submitted_ns": time.perf_counter_ns(),
                    }
                )
                with self.assertRaises(RuntimeError):
                    channel.finish()
            finally:
                channel.close()
            self.assertEqual(read_events(root / "refine-results.jsonl")[0]["status"], "failed")
            self.assertFalse(channel.process.is_alive())

    def test_resource_group_uses_same_observations_and_keeps_missing_as_unknown(self):
        def sample(t):
            return {
                "at_ns": t * 10**9,
                "roles": {
                    "zip": {"pid": 1, "sample": {"cpu_s": t, "rss_mib": 200}},
                    "refine": {"pid": 2, "sample": {"cpu_s": 1.5 * t, "rss_mib": 300}},
                    "producer": {"pid": 3, "sample": {"cpu_s": t, "rss_mib": 900}},
                },
            }

        rows = [sample(t) for t in (0, 1, 2)]
        result = resource_summary(rows, 0, 2 * 10**9, ["zip", "refine"])
        self.assertEqual(result["observed_model_cpu_percent"], 250)
        self.assertEqual(result["model_group_rss_observed_max_mib"], 500)
        rows[1]["roles"]["refine"]["sample"] = None
        self.assertIsNone(
            resource_summary(rows, 0, 2 * 10**9, ["zip", "refine"])["observed_model_cpu_percent"]
        )


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("prlimit"), "Linux/prlimit 필요"
)
class ConcurrentCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "configs").mkdir()
        for name in ("zipformer_paced.toml", "sensevoice_eval.toml"):
            text = (ROOT / "configs" / name).read_text().replace("chunk_ms = 100", "chunk_ms = 10")
            (self.root / "configs" / name).write_text(text)
        modules = self.root / "modules"
        modules.mkdir()
        (modules / "numpy.py").write_text(
            "from array import array\nfloat32='f'\ndef zeros(n,dtype):return array('f',[0])*n\ndef frombuffer(samples,dtype):return samples\n"
        )
        (modules / "sherpa_onnx.py").write_text("""
import time
from types import SimpleNamespace
class Stream:
    ready=False
    result=SimpleNamespace(text='<|ko|>가')
    def accept_waveform(self,rate,samples):self.ready=True
    def input_finished(self):pass
class OnlineRecognizer:
    @staticmethod
    def from_transducer(**kwargs):return OnlineRecognizer()
    def create_stream(self):return Stream()
    def is_ready(self,s):return s.ready
    def decode_stream(self,s):time.sleep(.001);s.ready=False
    def get_result(self,s):return '가'
class OfflineRecognizer:
    @staticmethod
    def from_sense_voice(**kwargs):return OfflineRecognizer()
    def create_stream(self):return Stream()
    def decode_stream(self,s):time.sleep(.03)
""")
        for name, files in {
            "sherpa-onnx-streaming-zipformer-korean-2024-06-16": [
                "tokens.txt",
                "encoder-epoch-99-avg-1.int8.onnx",
                "decoder-epoch-99-avg-1.onnx",
                "joiner-epoch-99-avg-1.int8.onnx",
            ],
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17": [
                "model.int8.onnx",
                "tokens.txt",
            ],
        }.items():
            model = self.root / "models" / name
            model.mkdir(parents=True)
            for file in files:
                (model / file).write_bytes(b"fixture")
        rows = []
        for i, n in enumerate((4103, 4107)):
            wav = self.root / f"{i}.wav"
            write_wav(wav, array("h", [100] * n))
            rows.append(
                {
                    "id": str(i),
                    "reference": "가",
                    "audio_path": str(wav),
                    "audio_sha256": digest(wav),
                }
            )
        (self.root / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.email=fixture@example.test",
                "-c",
                "user.name=fixture",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.env = {**os.environ, "PYTHONPATH": str(modules), "PYTHONDONTWRITEBYTECODE": "1"}

    def test_real_cli_spawns_refine_and_capture_and_replays_without_models(self):
        output = self.root / "run"
        command = [
            "prlimit",
            "--as=4294967296",
            "--core=0",
            "--",
            sys.executable,
            str(ROOT / "scripts/edge_asr.py"),
            "tools",
            "probe-concurrent",
            "--manifest",
            str(self.root / "manifest.jsonl"),
            "--output",
            str(output),
            "--zip-threads",
            "1",
            "--refine-threads",
            "1",
        ]
        result = subprocess.run(
            command, cwd=self.root, env=self.env, capture_output=True, text=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((output / "summary.json").read_text())
        self.assertTrue(summary["input_complete"])
        self.assertTrue(summary["refine"]["complete"])
        self.assertEqual(summary["zip_cer"]["cer"]["value"], 0)
        self.assertEqual(summary["refine"]["cer"]["cer"]["value"], 0)
        self.assertEqual(summary["refine"]["dropped_windows"], 0)
        self.assertGreater(summary["refine"]["zip_refine_call_overlap_s"], 0)
        shutil.rmtree(self.root / "models")
        self.assertEqual(summarize(output, self.root / "rescore.json"), summary)
        jobs = output / "refine-jobs.jsonl"
        original = jobs.read_bytes()
        rows = read_events(jobs)
        rows[0]["end"] += 1
        jobs.write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(ValueError):
            summarize(output, self.root / "invalid.json")
        jobs.write_bytes(original)

    def run_fixture(self, refine):
        output = self.root / "run"
        result = subprocess.run(
            [
                "prlimit",
                "--as=4294967296",
                "--core=0",
                "--",
                sys.executable,
                str(ROOT / "scripts/edge_asr.py"),
                "tools",
                "probe-concurrent",
                "--manifest",
                str(self.root / "manifest.jsonl"),
                "--output",
                str(output),
                "--zip-threads",
                "1",
                "--refine-threads",
                str(refine),
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result, json.loads((output / "summary.json").read_text())

    def test_streaming_only_control_does_not_invent_refine_metrics(self):
        result, summary = self.run_fixture(0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(summary["input_complete"])
        self.assertIsNone(summary["refine"])
        self.assertEqual(summary["resources"]["model_roles"], ["zip"])
        self.assertIsNone(summary["unmeasured"]["hybrid_merged_cer"])

    def test_refine_queue_loss_is_reported_without_hiding_valid_streaming(self):
        rows = []
        for i in range(5):
            wav = self.root / f"short-{i}.wav"
            write_wav(wav, array("h", [100] * 80))
            rows.append(
                {
                    "id": str(i),
                    "reference": "가",
                    "audio_path": str(wav),
                    "audio_sha256": digest(wav),
                }
            )
        (self.root / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        result, summary = self.run_fixture(1)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(summary["input_complete"])
        self.assertFalse(summary["refine"]["complete"])
        self.assertGreater(summary["refine"]["dropped_windows"], 0)
        self.assertEqual(summary["refine"]["enqueued"] + summary["refine"]["dropped_windows"], 5)
        self.assertIsNone(summary["refine"]["cer"]["cer"]["value"])
        self.assertEqual(summary["zip_cer"]["cer"]["value"], 0)


if __name__ == "__main__":
    unittest.main()
