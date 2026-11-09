"""토큰 구조 진단은 전사를 바꾸지 않으며 실제 모델 없이 검증한다."""

import copy
import json
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

from edge_asr.adapters.zipformer import ZipformerAdapter
from edge_asr.artifacts import read_jsonl
from edge_asr.legacy.token_alignment import (
    AlignmentAudit,
    TokenTraceReplay,
    inspect_snapshot,
    map_job,
    render_tokens,
)
from edge_asr.legacy.tools.probes.concurrent import probe
from edge_asr.legacy.tools.reports.token_alignment import summarize
from edge_asr.vad_probability import BoundedProbabilityVad
from tests.legacy import test_online_vad as fixtures


def snapshot(tokens=None, times=None, phase="partial", index=0):
    tokens = [" 안녕", " 하세요"] if tokens is None else tokens
    text, _ = render_tokens(tokens)
    return {
        "index": index,
        "source_id": "s",
        "source_start": 0,
        "phase": phase,
        "end": 48000,
        "text": text,
        "returned_ns": 1,
        "snapshot_read_ns": 2,
        "snapshot_read_wall_s": 0.0001,
        "tail_padding_s": 0.66,
        "native": {
            "text": " " + text,
            "tokens": tokens,
            "timestamps": [0.2, 1.2] if times is None else times,
            "segment": 0,
            "start_time": 0.0,
            "is_final": False,
        },
    }


class TokenAlignmentTests(unittest.TestCase):
    def test_unicode_bpe_render_tracks_character_ownership_and_rejects_unknown_codecs(self):
        text, origin = render_tokens([" 안녕", " 하", "세요", " ."])
        self.assertEqual(text, "안녕하세요.")
        self.assertEqual(origin, [0, 0, 1, 2, 2, 3])
        self.assertEqual(render_tokens([]), ("", []))
        for tokens in (["<0xEB>"], ["▁그"], [None], [""]):
            with self.assertRaises(ValueError):
                render_tokens(tokens)

    def test_invalid_time_origin_length_text_and_prefix_are_reported_without_repair(self):
        row = snapshot()
        self.assertTrue(inspect_snapshot(row)["valid"])
        for field, value in [
            ("timestamps", [0.2]),
            ("timestamps", [float("nan"), 1]),
            ("timestamps", [2, 1]),
            ("segment", 1),
            ("start_time", 1),
            ("text", "다른말"),
        ]:
            bad = copy.deepcopy(row)
            bad["native"][field] = value
            self.assertFalse(inspect_snapshot(bad)["valid"])
        later = snapshot([" 안녕", " 합니다"], [0.2, 1.2], index=1)
        self.assertIn("prefix_revised", inspect_snapshot(later, row)["reasons"])
        bad = copy.deepcopy(row)
        bad["native"]["tokens"] = None
        self.assertFalse(inspect_snapshot(bad, row)["valid"])

    def test_mapping_waits_for_right_context_and_holds_word_crossing(self):
        source = {"id": "s", "start": 0, "end": 48000}
        job = {"start": 0, "end": 16000}
        row = snapshot([" 안녕"], [0.2])
        self.assertIn("need_right_context", map_job(row, job, source)["reasons"])
        row = snapshot([" 안녕", " 하세요"], [0.2, 1.2])
        mapped = map_job(row, job, source)
        self.assertEqual(mapped["state"], "structural_candidate")
        self.assertEqual(mapped["zip_span_text"], "안녕")
        self.assertEqual(mapped["character_range"], [0, 2])
        self.assertFalse(mapped["acoustic_alignment_validated"])
        row = snapshot([" 안", "녕"], [0.2, 1.2])
        self.assertIn("right_word_continuation", map_job(row, job, source)["reasons"])
        self.assertEqual(map_job(row, job, source, False)["state"], "held")

    def test_nonzero_source_offsets_and_eof_are_distinct_from_audio_word_boundaries(self):
        source = {"id": "s", "start": 32000, "end": 80000}
        job = {"start": 32000, "end": 80000}
        row = snapshot([" 안녕"], [0.2], phase="final")
        row.update(source_start=32000, end=80000)
        mapped = map_job(row, job, source)
        self.assertEqual(mapped["state"], "structural_candidate")
        self.assertIsNone(mapped["token_durations"])
        row["native"]["timestamps"] = [3.1]
        self.assertTrue(inspect_snapshot(row)["valid"])
        self.assertIn("right_context_in_padding", map_job(row, job, source)["reasons"])
        row["native"]["timestamps"] = [4.0]
        self.assertFalse(inspect_snapshot(row)["valid"])

    def test_float_grid_boundary_ambiguity_is_held_without_rounding_native_time(self):
        row = snapshot([" 가", " 나"], [0.479999989, 1.2])
        result = map_job(row, {"start": 0, "end": 7680}, {"id": "s", "start": 0, "end": 48000})
        self.assertIn("float_boundary_ambiguity", result["reasons"])
        self.assertEqual(row["native"]["timestamps"][0], 0.479999989)

    def test_late_result_keeps_source_identity_and_future_revision_revokes_candidate(self):
        audit = AlignmentAudit([{"id": "s", "start": 0, "end": 48000, "reference": "사용 금지"}])
        job = {
            "index": 0,
            "source_id": "s",
            "start": 0,
            "end": 16000,
            "payload_float32_sha256": "a" * 64,
            "accepted": True,
        }
        audit.apply({"type": "job", "job": job})
        audit.apply({"type": "snapshot", "source_id": "s", "snapshot": snapshot()})
        change = audit.apply({"type": "receipt", "result": {**job, "status": "success"}})[0]
        self.assertEqual(change["state"], "structural_candidate")
        self.assertFalse(change["replacement_performed"])
        changed = snapshot([" 다르게", " 합니다"], [0.2, 1.2], index=1)
        change = audit.apply({"type": "snapshot", "source_id": "s", "snapshot": changed})[0]
        self.assertEqual(change["state"], "held")
        with self.assertRaises(ValueError):
            audit.apply({"type": "receipt", "result": {**job, "status": "success"}})
        with self.assertRaises(ValueError):
            audit.apply({"type": "job", "job": job, "reference": "정답"})

    def test_adapter_snapshot_never_decodes_or_mutates_stream_and_keeps_precision(self):
        raw = SimpleNamespace(
            text=" 가",
            tokens=[" 가"],
            timestamps=[0.1234567891],
            start_time=0.0,
            segment=0,
            is_final=False,
        )
        reads = []
        adapter = ZipformerAdapter({"path": ".", "tail_padding_s": 0.66}, 16000, Path("."))
        with self.assertRaises(RuntimeError):
            adapter.token_snapshot(object())
        stream = object()

        def native(s):
            reads.append(s)
            return raw

        adapter.recognizer = SimpleNamespace(recognizer=SimpleNamespace(get_result=native))
        self.assertEqual(adapter.token_snapshot(stream)["timestamps"], [0.1234567891])
        self.assertEqual(reads, [stream])
        adapter.recognizer = SimpleNamespace()
        with self.assertRaises(TypeError):
            adapter.token_snapshot(stream)

    def test_trace_preserves_all_continuous_chunks_and_creates_one_stream_per_source(self):
        class Model(fixtures.RecordingZip):
            model = {"tail_padding_s": 0.66}

            def __init__(self):
                super().__init__()
                self.streams = []

            def start_stream(self):
                stream = object()
                self.streams.append(stream)
                return stream

            def token_snapshot(self, stream):
                return snapshot()["native"] | {"text": "가", "tokens": [" 가"], "timestamps": [0.0]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior = root / "prior"
            prior.mkdir()
            baseline, _, samples = fixtures.OnlineVadCoreTests().replay(prior, "vad0")
            model = Model()
            with ExitStack() as stack:
                logs = [
                    stack.enter_context((root / name).open("x"))
                    for name in ("zip", "jobs", "tokens", "audit", "receipts")
                ]
                trace = TokenTraceReplay(
                    model,
                    baseline.sources,
                    {},
                    lambda job: ("enqueue", time.perf_counter_ns()),
                    logs[0],
                    logs[1],
                    vad=BoundedProbabilityVad(fixtures.Probability()),
                    policy="vad0",
                    token_log=logs[2],
                    audit_log=logs[3],
                    receipt_log=logs[4],
                )
                trace.start_stream()
                for start in range(0, len(samples), 1600):
                    trace.accept_at(
                        trace, samples[start : start + 1600], start, min(start + 1600, len(samples))
                    )
                trace.finish_stream(trace)
            self.assertEqual(model.blocks, baseline.adapter.blocks)
            self.assertEqual(len(model.streams), 2)
            self.assertEqual(
                [r["hypothesis"] for r in trace.source_rows],
                [r["hypothesis"] for r in baseline.source_rows],
            )

    def test_incompatible_modes_rejected_before_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kwargs in (
                {"segment_merge": True, "refine_policy": "vad0"},
                {"refine_policy": "fixed5"},
            ):
                with self.assertRaises(ValueError):
                    probe(
                        root, root / "missing", root / "out", 1, 2, token_diagnostics=True, **kwargs
                    )
            self.assertFalse((root / "out").exists())


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("prlimit"), "Linux/prlimit 필요"
)
class TokenAlignmentCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OnlineVadCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        path = self.root / "modules/sitecustomize.py"
        path.write_text(
            path.read_text()
            + """
import sherpa_onnx
from types import SimpleNamespace
sherpa_onnx.OnlineRecognizer.__init__=lambda self:setattr(self,'recognizer',SimpleNamespace(get_result=lambda s:SimpleNamespace(text=' 가',tokens=[] if os.environ.get('FIXTURE_BAD_TOKENS') else [' 가'],timestamps=[0.0],start_time=0.0,segment=0,is_final=False)))
"""
        )

    def run_fixture(self):
        return self.fixture.fixture.run_fixture(
            2,
            [
                "--refine-policy",
                "vad0",
                "--vad-model",
                str(self.fixture.vad_path),
                "--token-diagnostics",
            ],
        )

    def test_actual_ipc_and_snapshots_replay_without_models_and_never_replace(self):
        result, summary = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        d = summary["token_diagnostics"]
        self.assertTrue(d["complete"])
        self.assertEqual(d["valid_snapshots"], d["snapshots"])
        self.assertEqual(d["received_results"], 2)
        self.assertEqual(d["actual_replacements"], 0)
        self.assertIsNone(d["candidate_merged_cer"])
        self.assertEqual(summary["zip_cer"]["cer"]["value"], 0)
        shutil.rmtree(self.root / "models")
        for p in self.root.glob("*.wav"):
            p.unlink()
        self.assertEqual(summarize(self.root / "run", self.root / "rescore.json"), summary)
        path = self.root / "run/token-alignment-events.jsonl"
        rows = read_jsonl(path)
        rows[-1]["changes"] = [{"bad": True}]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(ValueError):
            summarize(self.root / "run", self.root / "invalid.json")

    def test_invalid_native_details_hold_candidates_without_changing_baseline(self):
        self.fixture.fixture.env["FIXTURE_BAD_TOKENS"] = "1"
        result, summary = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(summary["zip_cer"]["cer"]["value"], 0)
        self.assertEqual(summary["token_diagnostics"]["valid_snapshots"], 0)
        self.assertEqual(summary["token_diagnostics"]["actual_replacements"], 0)
        self.assertEqual(summary["token_diagnostics"]["final_states"], {"held": 2})

    def test_failed_vad_retains_incomplete_measurement_and_reaps_child(self):
        self.fixture.fixture.env["FIXTURE_VAD_FAILURE"] = "1"
        result, summary = self.run_fixture()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(summary["token_diagnostics"]["complete"])
        worker = json.loads((self.root / "run/worker.json").read_text())
        self.assertFalse(worker["refine_alive"])
