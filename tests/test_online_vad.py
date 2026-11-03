"""실제 모델 없이 온라인 VAD 재조립·방출·문맥·유한 큐와 재집계를 확인한다."""

import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from array import array
from pathlib import Path

import test_concurrent_probe as fixtures

from edge_asr.artifacts import read_jsonl
from edge_asr.online_vad import OnlineVadReplay, TimedProbability
from edge_asr.tools.probes.concurrent import probe
from edge_asr.tools.reports.concurrent import summarize
from edge_asr.vad_probability import BoundedProbabilityVad


class Probability:
    def __init__(self, value=1):
        self.value = value
        self.reset_count = 0

    def reset(self):
        self.reset_count += 1

    def __call__(self, block):
        if self.value is None:
            raise RuntimeError("fixture VAD failure")
        return self.value


class RecordingZip(fixtures.StreamingFixture):
    def __init__(self):
        super().__init__()
        self.blocks = []

    def accept_chunk(self, stream, samples):
        self.blocks.append(array("f", samples))
        return "가"


class OnlineVadCoreTests(unittest.TestCase):
    def test_bad_policy_refine_control_and_vad_hash_fail_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / "bad.onnx"
            bad.write_bytes(b"bad")
            for policy, refine in (("unknown", 2), ("vad0", 0), ("vad0", 2)):
                with self.subTest(policy=policy, refine=refine), self.assertRaises(ValueError):
                    probe(
                        root,
                        root / "missing.jsonl",
                        root / "out",
                        1,
                        refine,
                        refine_policy=policy,
                        vad_model=bad,
                    )
                self.assertFalse((root / "out").exists())

    def replay(self, root, policy, value=1):
        lengths = (13 * 16000 + 137, 4107)
        samples = array("f", ((i % 200 - 100) / 32768 for i in range(sum(lengths))))
        sources = [
            {"id": "a", "reference": "가", "audio_sha256": "x", "start": 0, "end": lengths[0]},
            {
                "id": "b",
                "reference": "가",
                "audio_sha256": "y",
                "start": lengths[0],
                "end": len(samples),
            },
        ]
        received = []

        def submit(job):
            received.append(job)
            return "enqueue", time.perf_counter_ns()

        probability = Probability(value)
        vad = BoundedProbabilityVad(TimedProbability(probability))
        model = RecordingZip()
        with (root / "zip").open("x") as z, (root / "jobs").open("x") as j:
            replay = OnlineVadReplay(model, sources, {}, submit, z, j, vad=vad, policy=policy)
            replay.start_stream()
            before = probability.reset_count
            for start in range(0, len(samples), 1600):
                replay.accept_at(
                    replay, samples[start : start + 1600], start, min(start + 1600, len(samples))
                )
            replay.finish_stream(replay)
        self.assertEqual(probability.reset_count - before, 2)
        self.assertEqual(array("f", (x for block in model.blocks for x in block)), samples)
        return replay, received, samples

    def test_reassembly_at_unaligned_file_edges_preserves_source_pcm_and_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, jobs, samples = self.replay(Path(directory), "vad0")
            self.assertEqual(len(jobs), 4)
            for job in jobs:
                self.assertEqual(job["samples"], samples[job["start"] : job["end"]])
                self.assertEqual(job["context_start"], job["start"])
                self.assertLessEqual(job["emitted_ns"], job["submitted_ns"])
            self.assertTrue(all(r["pipeline"]["complete"] for r in replay.vad_sources))
            self.assertLessEqual(max(r["reassembly_peak_frames"] for r in replay.vad_sources), 3199)
            model = RecordingZip()
            with (
                (Path(directory) / "fixed-zip").open("x") as z,
                (Path(directory) / "fixed-jobs").open("x") as j,
            ):
                fixed = fixtures.ConcurrentReplay(model, replay.sources, {}, None, z, j)
                fixed.start_stream()
                for start in range(0, len(samples), 1600):
                    fixed.accept_at(
                        fixed, samples[start : start + 1600], start, min(start + 1600, len(samples))
                    )
                fixed.finish_stream(fixed)
            self.assertEqual(model.blocks, replay.adapter.blocks)

    def test_continuation_uses_only_real_past_pcm_and_cannot_cross_source(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, jobs, samples = self.replay(Path(directory), "vad-continuation200")
            self.assertEqual([j["context_start"] for j in jobs[:3]], [0, 76800, 156800])
            self.assertEqual(jobs[-1]["context_start"], replay.sources[1]["start"])
            self.assertEqual(
                [j["segment"]["is_continuation"] for j in jobs], [False, True, True, False]
            )
            for job in jobs:
                self.assertEqual(job["samples"], samples[job["context_start"] : job["end"]])
                self.assertLessEqual(job["payload_frames"], 83200)

    def test_empty_vad_emits_no_refine_job_while_zip_keeps_all_input(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, jobs, _ = self.replay(Path(directory), "vad0", value=0)
            self.assertEqual(jobs, [])
            self.assertTrue(
                all(r["status"] == "success" and not r["segments"] for r in replay.vad_sources)
            )


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("prlimit"), "Linux/prlimit 필요"
)
class OnlineVadCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConcurrentCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        modules = self.root / "modules"
        (modules / "sitecustomize.py").write_text("""
import hashlib,os
from types import SimpleNamespace
import edge_asr.vad as spec
import edge_asr.vad_probability as runtime
spec.MODEL_SHA=hashlib.sha256(b'fixture-vad').hexdigest()
class Probability:
    def __init__(self,path):
        self.reset_count=0
        self.np=SimpleNamespace(asarray=lambda x,**kw:x,float32='mock')
    def reset(self):self.reset_count+=1;self.windows=0
    def __call__(self,block):
        self.windows+=1
        if os.environ.get('FIXTURE_VAD_WARM_FAILURE') or (os.environ.get('FIXTURE_VAD_FAILURE') and self.reset_count>=5):raise RuntimeError('fixture VAD failure')
        if os.environ.get('FIXTURE_VAD_EARLY_END'):return float(self.windows<=6)
        return 0.0 if os.environ.get('FIXTURE_VAD_SILENT') else 1.0
    def metadata(self):return {'reset_count':self.reset_count,'backend':'fixture'}
runtime.K2SileroProbability=Probability
""")
        self.fixture.env["PYTHONPATH"] += os.pathsep + str(fixtures.ROOT / "src")
        self.vad_path = self.root / "vad.onnx"
        self.vad_path.write_bytes(b"fixture-vad")

    def run_policy(self, policy="vad0"):
        return self.fixture.run_fixture(
            2, ["--refine-policy", policy, "--vad-model", str(self.vad_path)]
        )

    def test_real_cli_has_online_vad_cost_and_model_free_replay_with_ipc_geometry(self):
        result, summary = self.run_policy("vad-continuation200")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(summary["input_complete"])
        self.assertTrue(summary["refine"]["complete"])
        self.assertEqual(summary["refine"]["cer"]["cer"]["value"], 0)
        self.assertEqual(summary["refine"]["no_vad_segment_sources"], 0)
        self.assertEqual(summary["refine"]["vad_emission_to_return_s"]["n"], 2)
        worker = json.loads((self.root / "run/worker.json").read_text())
        self.assertEqual(worker["vad_warmup_runs"], 3)
        self.assertEqual(worker["vad_settings"]["num_threads"], 1)
        self.assertEqual(worker["vad_settings"]["window_frames"], 512)
        self.assertGreater(worker["vad_inference"]["calls"], 0)
        self.assertEqual(
            worker["vad_measurement_after"]["reset_count"]
            - worker["vad_measurement_before"]["reset_count"],
            2,
        )
        self.assertIn("online_vad", summary["resources"]["main_pid_components"])
        self.assertEqual(summarize(self.root / "run", self.root / "rescore.json"), summary)
        path = self.root / "run/refine-jobs.jsonl"
        rows = read_jsonl(path)
        bad = copy.deepcopy(rows)
        bad[0]["context_start"] += 1
        path.write_text("".join(json.dumps(r) + "\n" for r in bad))
        with self.assertRaises(ValueError):
            summarize(self.root / "run", self.root / "bad.json")

    def test_silence_is_empty_hypothesis_and_null_latency_without_asr_fallback(self):
        self.fixture.env["FIXTURE_VAD_SILENT"] = "1"
        result, summary = self.run_policy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(summary["refine"]["successful"], 0)
        self.assertEqual(summary["refine"]["no_vad_segment_sources"], 2)
        self.assertEqual(summary["refine"]["cer"]["cer"]["deletions"], 2)
        self.assertIsNone(summary["refine"]["window_end_to_return_s"]["mean"])
        self.assertTrue(summary["refine"]["complete"])

    def test_native_vad_failure_is_failure_with_partial_evidence_and_child_cleanup(self):
        self.fixture.env["FIXTURE_VAD_FAILURE"] = "1"
        result, summary = self.run_policy()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(summary["input_complete"])
        self.assertFalse(summary["refine"]["complete"])
        self.assertIsNone(summary["refine"]["cer"]["cer"]["value"])
        worker = json.loads((self.root / "run/worker.json").read_text())
        self.assertFalse(worker["refine_alive"])

    def test_vad_warmup_failure_has_no_fabricated_measurement_and_cleans_refine(self):
        self.fixture.env["FIXTURE_VAD_WARM_FAILURE"] = "1"
        result, summary = self.run_policy()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(summary["input_complete"])
        self.assertIsNone(summary["refine"])
        self.assertIsNone(summary["resources"])
        worker = json.loads((self.root / "run/worker.json").read_text())
        self.assertFalse(worker["refine_alive"])

    def test_online_vad_queue_loss_keeps_complete_zip_and_null_refine_cer(self):
        module = self.root / "modules/sherpa_onnx.py"
        module.write_text(module.read_text().replace("time.sleep(.03)", "time.sleep(.4)"))
        rows = []
        for i in range(5):
            path = self.root / f"voice-{i}.wav"
            fixtures.write_wav(path, array("h", [100] * 2560))
            rows.append(
                {
                    "id": str(i),
                    "reference": "가",
                    "audio_path": str(path),
                    "audio_sha256": fixtures.digest(path),
                }
            )
        (self.root / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        result, summary = self.run_policy()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(summary["input_complete"])
        self.assertGreater(summary["refine"]["dropped_windows"], 0)
        self.assertFalse(summary["refine"]["complete"])
        self.assertIsNone(summary["refine"]["cer"]["cer"]["value"])
        self.assertEqual(summary["zip_cer"]["cer"]["value"], 0)

    def test_refine_can_return_before_file_end_without_negative_endpoint_latency(self):
        self.fixture.env["FIXTURE_VAD_EARLY_END"] = "1"
        path = self.root / "early.wav"
        fixtures.write_wav(path, array("h", [100] * 16000))
        (self.root / "manifest.jsonl").write_text(
            json.dumps(
                {
                    "id": "early",
                    "reference": "가",
                    "audio_path": str(path),
                    "audio_sha256": fixtures.digest(path),
                }
            )
            + "\n"
        )
        result, summary = self.run_policy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(summary["refine"]["final_from_source_file_end_s"]["mean"], 0)
        self.assertGreater(summary["refine"]["window_end_to_return_s"]["mean"], 0)
        self.assertIsNone(summary["unmeasured"]["acoustic_endpoint_latency_s"])
