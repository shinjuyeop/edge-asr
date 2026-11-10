"""계측 경계·TTFT 구분과 원본 C API 전달을 검사한다. 실제 ASR 추론 아님."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr.qwen import profiling

ROOT = Path(__file__).resolve().parents[2]


class ProfilingTests(unittest.TestCase):
    def test_boundaries_separate_session_time_native_gaps_and_first_logits(self):
        events = [
            {"kind": k, "begin_ns": b, "end_ns": e, "failed": 0}
            for k, b, e in ((1, 20, 30), (2, 35, 55), (3, 60, 100), (4, 110, 140), (4, 150, 180))
        ]
        boundaries = {"decode_stream": {"begin_ns": 15, "end_ns": 190, "elapsed_s": 175e-9}}
        r = profiling.summarize(events, boundaries, 0, 200, True)
        self.assertEqual(r["ort_token_decode_calls"], 2)
        self.assertAlmostEqual(r["ort_encoder_s"], 20e-9)
        self.assertAlmostEqual(r["ort_token_decode_s"], 60e-9)
        self.assertAlmostEqual(r["native_non_ort_s"], 45e-9)
        self.assertAlmostEqual(r["decoder_inter_run_gap_s"], 20e-9)
        self.assertAlmostEqual(r["first_logits_ready_s"], 100e-9)
        self.assertAlmostEqual(r["first_decode_entry_s"], 110e-9)
        self.assertIsNone(r["ttft_s"])

    def test_disabled_does_not_fill_unmeasured_session_time_with_zero(self):
        r = profiling.summarize([], {}, 0, 100, False)
        self.assertIsNone(r["ort_encoder_s"])
        self.assertIsNone(r["ort_token_decode_calls"])
        with self.assertRaises(ValueError):
            profiling.summarize([{"kind": 1}], {}, 0, 100, False)

    def test_unknown_order_failed_calls_and_clock_mismatch_are_rejected(self):
        events = [
            {"kind": k, "begin_ns": i * 10, "end_ns": i * 10 + 5, "failed": 0}
            for i, k in enumerate((1, 2, 3))
        ]
        boundary = {"decode_stream": {"begin_ns": 0, "end_ns": 50, "elapsed_s": 50e-9}}
        for change in ("kind", "failed", "begin_ns"):
            altered = [dict(e) for e in events]
            altered[1][change] = {"kind": 0, "failed": 1, "begin_ns": -1}[change]
            with self.subTest(change=change), self.assertRaises(ValueError):
                profiling.summarize(altered, boundary, 0, 100, True)

    def test_native_failure_metadata_survives_wrapper_and_abba_warmup_stays_off(self):
        inner = Mock()
        inner.model_files = {}
        inner.metadata = {"fixture": True}
        inner.last_result_metadata = {"native_stderr": "fixture repetition loop"}
        inner.transcribe.side_effect = RuntimeError("fixture native rejection")
        trace = Mock()
        trace.end.return_value = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.so"
            path.write_bytes(b"not a native library")
            with patch.object(profiling, "Qwen3Adapter", return_value=inner):
                adapter = profiling.ProfiledAdapter(
                    {}, 16000, ROOT, path, "abba", trace_factory=lambda p: trace
                )
            adapter.prepare_call("warmup", "x", 0)
            self.assertFalse(adapter.enabled)
            for repeat, expected in ((1, False), (2, True), (3, True), (4, False)):
                adapter.prepare_call("steady", "x", repeat)
                self.assertEqual(adapter.enabled, expected)
            with self.assertRaisesRegex(RuntimeError, "native rejection"):
                adapter.transcribe(None)
            self.assertEqual(
                adapter.last_result_metadata["native_stderr"], "fixture repetition loop"
            )
            self.assertFalse(adapter.last_result_metadata["profile"]["enabled"])
            adapter.close()
            inner.close.assert_called_once()


@unittest.skipUnless(
    os.environ.get("EDGE_ASR_ORT_HEADERS") and shutil.which("g++"),
    "native mock 검사는 g++와 별도 ORT 1.28.2 헤더 경로가 필요합니다.",
)
class NativeTraceTests(unittest.TestCase):
    def test_native_api_is_forwarded_and_scopes_reset_without_models(self):
        headers = Path(os.environ["EDGE_ASR_ORT_HEADERS"]).resolve()
        fixture = ROOT / "tests/qwen/fixtures/fake_ort.cc"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "native/ort_trace/build.py"),
                    "--headers",
                    str(headers),
                    "--output",
                    str(root / "trace"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            for args in (
                ["-DEDGE_FAKE_ORT", "-shared", "-fPIC", "-o", str(root / "fake.so")],
                ["-ldl", "-o", str(root / "client")],
            ):
                result = subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-O2",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-I",
                        str(headers),
                        str(fixture),
                        *args,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(
                [str(root / "client")],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "LD_PRELOAD": str(root / "trace/libedge_asr_ort_trace.so"),
                    "EDGE_ASR_ORT_LIBRARY": str(root / "fake.so"),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("native mock API forwarding", result.stdout)
            receipt = json.loads((root / "trace/build.json").read_text())
            self.assertIsNotNone(receipt["library_sha256"])


if __name__ == "__main__":
    unittest.main()
