"""실제 ASR 대신 모의 프로브로 반복 실행·실패·계측 경계를 검사한다."""

import json
from pathlib import Path
import platform
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from edge_asr import benchmark as bench

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = """--- Summary ---
Audio duration: 1.000s
First partial: 0.2
Final result: 테스트
Final elapsed: 1.100s
Finalization delay: 0.100s
Max processing overrun: 12.5ms
"""


def fake_probe(path: Path, summary=SUMMARY) -> None:
    path.write_text("import time\ntime.sleep(0.03)\nprint(" + repr(summary) + ")\n", encoding="utf-8")


def fake_assets(root: Path, channels=1) -> None:
    for name in bench.MODEL_FILES:
        path = root / bench.MODEL / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".wav":
            with wave.open(str(path), "wb") as audio:
                audio.setparams((channels, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 16000 * channels)
        else:
            path.write_bytes(b"synthetic model fixture, not a real model")


class BenchmarkParsingTests(unittest.TestCase):
    def test_preserved_results(self):
        for threads, expected in ((1, 0.080), (2, 0.064), (4, 0.092)):
            with self.subTest(threads=threads):
                text = (ROOT / f"experiments/m1_zipformer/results/streaming_threads_{threads}.txt").read_text()
                result = bench.parse_probe(text)
                self.assertEqual(result["audio_duration_s"], 3.526)
                self.assertEqual(result["finalization_from_audio_end_s"], expected)

    def test_no_partial_is_missing(self):
        result = bench.parse_probe(SUMMARY.replace("First partial: 0.2", "First partial: None"))
        self.assertIsNone(result["first_partial_from_audio_start_s"])

    def test_invalid_output_rejected(self):
        for text in ("", SUMMARY + SUMMARY,
                     SUMMARY.replace("0.2", "nan"),
                     SUMMARY.replace("1.000s", "0.000s"),
                     SUMMARY.replace("Final elapsed: 1.100s", "Final elapsed: 9.000s"),
                     SUMMARY.replace("First partial: 0.2", "First partial: 9.0")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                bench.parse_probe(text)

    def test_nearest_rank_and_missing_statistics(self):
        stats = bench.distribution(list(range(1, 21)))
        self.assertEqual((stats["p50"], stats["p95"]), (10, 19))
        self.assertIsNone(bench.distribution([])["mean"])
        self.assertIsNone(bench.distribution([1.0])["p95"])

    def test_rotated_schedule(self):
        plan = bench.schedule([1, 2, 4], 3, 30)
        self.assertEqual(len(plan), 99)
        self.assertEqual([j["threads"] for j in plan[0:9]], [1, 2, 4, 2, 4, 1, 4, 1, 2])
        for threads in (1, 2, 4):
            self.assertEqual(sum(j["threads"] == threads and j["phase"] == "measurement" for j in plan), 30)

    def test_asset_format_and_fingerprints(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_assets(root)
            assets = bench.read_assets(root)
            self.assertEqual(assets["duration_s"], 1)
            self.assertEqual(len(assets["sha256"]), 5)
            fake_assets(root, channels=2)
            with self.assertRaises(ValueError):
                bench.read_assets(root)


@unittest.skipUnless(platform.system() == "Linux", "Linux 프로세스 계측 검사")
class BenchmarkProcessTests(unittest.TestCase):
    def run_probe(self, temp, script, timeout=2):
        root = Path(temp)
        probe = root / "fake.py"
        probe.write_text(script, encoding="utf-8")
        return bench.run_one(sys.executable, probe, root, root / "run",
                             {"phase": "measurement", "threads": 2, "round": 1}, timeout, 0.005)

    def test_success_collects_resources_and_raw_log(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_probe(temp, "import time\ntime.sleep(0.05)\nprint(" + repr(SUMMARY) + ")")
            self.assertEqual(result["status"], "success")
            self.assertGreater(result["resources"]["sample_count"], 0)
            self.assertIsNotNone(result["resources"]["process_cpu_percent"])
            self.assertTrue((Path(temp) / "run/resources.jsonl").exists())

    def test_nonzero_exit_not_accepted_even_with_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_probe(temp, "print(" + repr(SUMMARY) + ")\nraise SystemExit(7)")
            self.assertEqual((result["status"], result["returncode"]), ("failed", 7))
            self.assertEqual(result["metrics"], {})

    def test_timeout_stops_process(self):
        with tempfile.TemporaryDirectory() as temp:
            started = time.perf_counter()
            result = self.run_probe(temp, "import time\ntime.sleep(10)", timeout=0.05)
            self.assertEqual(result["status"], "timeout")
            self.assertLess(time.perf_counter() - started, 2)

    def test_interrupt_stops_process_and_retains_record(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bench, "process_sample", side_effect=KeyboardInterrupt):
                result = self.run_probe(temp, "import time\ntime.sleep(10)")
            self.assertEqual(result["status"], "interrupted")
            self.assertIsNotNone(result["returncode"])
            self.assertTrue((Path(temp) / "run/result.json").exists())

    def test_invalid_output_retains_log(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_probe(temp, "print('incomplete result')")
            self.assertEqual(result["status"], "invalid_output")
            self.assertIn("incomplete result", (Path(temp) / "run/probe.txt").read_text())

    def test_suite_excludes_warmups_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_assets(root)
            probe = root / "fake.py"
            fake_probe(probe)
            output = root / "outputs"
            output.mkdir()
            (output / "existing.txt").write_text("keep")
            original_hash = bench.digest(probe)
            with patch.object(bench, "target_environment", return_value={"synthetic_test": True}):
                code = bench.main(["--asset-root", str(root), "--probe", str(probe),
                                   "--output-dir", str(output), "--warmups", "1", "--repetitions", "2",
                                   "--sample-interval-ms", "5"])
            self.assertEqual(code, 0)
            run = next(p for p in output.iterdir() if p.is_dir())
            summary = json.loads((run / "summary.json").read_text())
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(summary["warmup_attempted"], 3)
            for count in ("1", "2", "4"):
                group = summary["threads"][count]
                self.assertEqual(group["successful"], 2)
                self.assertEqual(group["metrics"]["first_partial_from_audio_start_s"]["n"], 2)
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["python_executable"], sys.executable)
            self.assertEqual((output / "existing.txt").read_text(), "keep")
            self.assertEqual(bench.digest(probe), original_hash)

    def test_failed_warmup_aborts_with_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_assets(root)
            probe = root / "fail.py"
            probe.write_text("raise SystemExit(1)")
            with patch.object(bench, "target_environment", return_value={"synthetic_test": True}):
                code = bench.main(["--asset-root", str(root), "--probe", str(probe),
                                   "--output-dir", str(root / "outputs"), "--warmups", "1",
                                   "--repetitions", "2", "--sample-interval-ms", "5"])
            run = next((root / "outputs").iterdir())
            summary = json.loads((run / "summary.json").read_text())
            self.assertEqual(code, 1)
            self.assertEqual(summary["warmup_failed"], 1)
            self.assertEqual(summary["threads"]["2"]["attempted"], 0)

    def test_missing_packages_leave_failure_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            probe = root / "fake.py"
            fake_probe(probe)
            with patch.object(bench, "target_environment", side_effect=ValueError("synthetic missing runtime")):
                code = bench.main(["--asset-root", str(root), "--probe", str(probe),
                                   "--output-dir", str(root / "outputs")])
            run = next((root / "outputs").iterdir())
            self.assertEqual(code, 2)
            self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "preflight_or_runner_error")
            self.assertEqual(json.loads((run / "runs.json").read_text()), [])

    def test_measurement_failure_continues_other_threads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_assets(root)
            probe = root / "fake.py"
            probe.write_text("import os, sys\nif os.environ['ASR_THREADS'] == '1': sys.exit(3)\nprint(" + repr(SUMMARY) + ")")
            with patch.object(bench, "target_environment", return_value={"synthetic_test": True}):
                code = bench.main(["--asset-root", str(root), "--probe", str(probe),
                                   "--output-dir", str(root / "outputs"), "--warmups", "0",
                                   "--repetitions", "1", "--threads", "1", "2", "--sample-interval-ms", "5"])
            run = next((root / "outputs").iterdir())
            summary = json.loads((run / "summary.json").read_text())
            self.assertEqual(code, 1)
            self.assertEqual(summary["threads"]["1"]["failed"], 1)
            self.assertEqual(summary["threads"]["2"]["successful"], 1)
            self.assertIsNone(summary["threads"]["2"]["metrics"]["final_elapsed_s"]["p95"])
            self.assertTrue((run / "summary.md").exists())
