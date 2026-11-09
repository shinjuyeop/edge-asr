"""실제 모델 없이 VAD/Zip 동일 PCM·살아 있는 결과 수신·병합 재현을 검증한다."""

import json
import shutil
import sys
import tempfile
import time
import unittest
from array import array
from contextlib import ExitStack
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl
from edge_asr.legacy.merging import Merger
from edge_asr.legacy.segment_merge import SegmentMergeReplay
from edge_asr.legacy.tools.probes.concurrent import probe
from edge_asr.legacy.tools.reports.segment_merge import summarize
from edge_asr.vad_probability import BoundedProbabilityVad
from tests.legacy import test_concurrent_probe as common
from tests.legacy import test_online_vad as online


class RecordingZip(common.StreamingFixture):
    def __init__(self):
        self.streams = []

    def start_stream(self):
        stream = array("f")
        self.streams.append(stream)
        return stream

    def accept_chunk(self, stream, samples):
        stream.extend(samples)
        return "가"


class SegmentMergeCoreTests(unittest.TestCase):
    def run_core(self, root, probability=1):
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
        jobs = []

        def submit(job):
            jobs.append(job)
            return "enqueue", time.perf_counter_ns()

        model = RecordingZip()
        with ExitStack() as stack:
            handles = {
                k: stack.enter_context((root / k).open("x"))
                for k in ("zip", "jobs", "merge", "segments", "receipts")
            }
            replay = SegmentMergeReplay(
                model,
                sources,
                {},
                submit,
                handles["zip"],
                handles["jobs"],
                vad=BoundedProbabilityVad(
                    probability if callable(probability) else online.Probability(probability)
                ),
                policy="vad0",
                merge_log=handles["merge"],
                segment_log=handles["segments"],
                receipt_log=handles["receipts"],
            )
            replay.start_stream()
            for start in range(0, len(samples), 1600):
                replay.accept_at(
                    replay, samples[start : start + 1600], start, min(start + 1600, len(samples))
                )
            replay.finish_stream(replay)
            for job in reversed(jobs):
                replay.receive(
                    {k: v for k, v in job.items() if k != "samples"}
                    | {
                        "hypothesis": f"수정{job['index']}",
                        "status": "success",
                        "ended_ns": time.perf_counter_ns(),
                    },
                    time.perf_counter_ns(),
                )
            replay.drain_finished()
        return replay, model, jobs, samples

    def test_streaming_prefix_hard_split_and_unaligned_sources_share_exact_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, model, jobs, samples = self.run_core(Path(directory))
            self.assertEqual(len(jobs), 4)
            for stream, job, segment in zip(model.streams, jobs, replay.segments):
                self.assertEqual(stream, job["samples"])
                self.assertEqual(stream, samples[job["start"] : job["end"]])
                self.assertLessEqual(len(stream), 80000)
                self.assertEqual(segment["segment_key"], job["segment_key"])
            self.assertTrue(replay.segments[0]["first_partial_before_emission"])
            self.assertFalse(replay.segments[-1]["first_partial_before_emission"])
            finals = [s.decision for s in replay.engines["refine_complete"].sources.values()]
            self.assertEqual([x["text"] for x in finals], ["수정0", "수정1", "수정2", "수정3"])
            self.assertTrue(finals[-1]["source_id"].startswith("b::"))

    def test_retrospective_vad_end_never_discards_audio_already_fed_to_zip(self):
        class Ends:
            def reset(self):
                self.n = 0

            def __call__(self, samples):
                self.n += 1
                return float(self.n <= 25 or 55 < self.n <= 80)

        with tempfile.TemporaryDirectory() as directory:
            replay, model, jobs, samples = self.run_core(Path(directory), Ends())
            self.assertGreaterEqual(len(jobs), 3)
            for stream, job in zip(model.streams, jobs):
                self.assertEqual(stream, samples[job["start"] : job["end"]])
            self.assertGreater(jobs[1]["start"], jobs[0]["end"])
            self.assertTrue(any(s["first_partial_before_emission"] for s in replay.segments))

    def test_no_vad_calls_neither_asr_and_never_fabricates_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, model, jobs, _ = self.run_core(Path(directory), probability=0)
            self.assertEqual(model.streams, [])
            self.assertEqual(jobs, [])
            self.assertTrue(
                all(
                    s["hypothesis"] == "" and s["first_partial_ns"] is None
                    for s in replay.source_rows
                )
            )
            self.assertTrue(replay.drained)

    def test_provisional_end_is_unknown_until_closed_and_cannot_discard_fed_frames(self):
        engine = Merger("s", "zip_only")
        engine.register("a", 0, 80000, end_is_bound=True)
        engine.apply(
            {
                "type": "partial",
                "source_id": "a",
                "session_id": "s",
                "at_ns": 1,
                "end": 3200,
                "text": "가",
            }
        )
        self.assertIsNone(engine.updates[0]["end"])
        self.assertEqual(engine.updates[0]["end_bound"], 80000)
        with self.assertRaises(ValueError):
            engine.apply(
                {
                    "type": "close",
                    "source_id": "a",
                    "session_id": "s",
                    "at_ns": 2,
                    "end": 3000,
                    "text": "가",
                }
            )
        engine.apply(
            {
                "type": "close",
                "source_id": "a",
                "session_id": "s",
                "at_ns": 3,
                "end": 4000,
                "text": "가",
            }
        )
        self.assertEqual(engine.sources["a"].decision["end"], 4000)
        engine.register("b", 4000, 10000, end_is_bound=True)

    def test_unsupported_context_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                probe(
                    root,
                    root / "missing",
                    root / "out",
                    1,
                    2,
                    segment_merge=True,
                    refine_policy="vad-continuation200",
                )
            self.assertFalse((root / "out").exists())


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("prlimit"), "Linux/prlimit 필요"
)
class SegmentMergeCliTests(unittest.TestCase):
    def setUp(self):
        self.online = online.OnlineVadCliTests()
        self.online.setUp()
        self.addCleanup(self.online.doCleanups)
        self.fixture = self.online.fixture
        self.root = self.online.root

    def run_fixture(self):
        return self.fixture.run_fixture(
            2,
            [
                "--refine-policy",
                "vad0",
                "--vad-model",
                str(self.online.vad_path),
                "--segment-merge",
            ],
        )

    def test_actual_ipc_receipt_live_apply_and_model_free_rescore(self):
        result, summary = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        merge = summary["segment_merge"]
        self.assertTrue(merge["complete"])
        self.assertEqual(merge["same_actual_pcm_segments"], 2)
        self.assertEqual(merge["received_results"], 2)
        self.assertGreaterEqual(merge["child_end_to_parent_receive_s"]["mean"], 0)
        self.assertEqual(merge["policies"]["refine_complete"]["cer"]["cer"]["value"], 0)
        self.assertIsNone(summary["unmeasured"]["ui_display_latency_s"])
        raw = self.root / "run"
        hashes = {p.name: digest(p) for p in raw.iterdir() if p.is_file()}
        shutil.rmtree(self.root / "models")
        for p in self.root.glob("*.wav"):
            p.unlink()
        self.assertEqual(summarize(raw, self.root / "rescore.json"), summary)
        self.assertEqual(hashes, {p.name: digest(p) for p in raw.iterdir() if p.is_file()})
        rows = read_jsonl(raw / "merge-events.jsonl")
        self.assertTrue(any(r.get("received_ns") is not None for r in rows))
        rows[-2]["applied_ns"] = 0
        (raw / "merge-events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(ValueError):
            summarize(raw, self.root / "bad.json")

    def test_silent_path_is_empty_with_null_latency_and_no_fake_zero(self):
        self.fixture.env["FIXTURE_VAD_SILENT"] = "1"
        result, summary = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        merge = summary["segment_merge"]
        self.assertTrue(merge["complete"])
        self.assertEqual(merge["same_actual_pcm_segments"], 0)
        self.assertIsNone(merge["child_end_to_parent_receive_s"]["mean"])
        self.assertEqual(merge["policies"]["refine_complete"]["cer"]["cer"]["deletions"], 2)

    def test_refine_failure_is_received_and_child_is_reaped(self):
        module = self.root / "modules/sherpa_onnx.py"
        module.write_text(
            module.read_text().replace(
                "def decode_stream(self,s):time.sleep(.03)",
                "def decode_stream(self,s):\n        self.calls=getattr(self,'calls',0)+1\n        if self.calls>3:raise RuntimeError('fixture refine failure')\n        time.sleep(.03)",
            )
        )
        result, summary = self.run_fixture()
        self.assertEqual(result.returncode, 1)
        worker = json.loads((self.root / "run/worker.json").read_text())
        self.assertFalse(worker["refine_alive"])
        self.assertEqual(worker["status"], "measurement_failed")
        self.assertFalse(summary["segment_merge"]["complete"])
        self.assertIsNone(
            summary["segment_merge"]["policies"]["refine_complete"]["cer"]["cer"]["value"]
        )

    def test_vad_failure_retains_failed_measurement_and_reaps_children(self):
        self.fixture.env["FIXTURE_VAD_FAILURE"] = "1"
        result, _ = self.run_fixture()
        self.assertEqual(result.returncode, 1)
        worker = json.loads((self.root / "run/worker.json").read_text())
        self.assertEqual(worker["status"], "measurement_failed")
        self.assertFalse(worker["refine_alive"])
