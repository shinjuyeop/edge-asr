"""네이티브/패키지 버전 구분과 Linux TID 차분 진단. ASR 런타임 실행 없음."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from edge_asr.diagnostics import observe_threads, thread_snapshot
from edge_asr.runtime import runtime_info

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def test_python_package_and_native_versions_are_not_substituted(self):
        module = types.SimpleNamespace(onnxruntime_version="native-1.28.2")
        with patch.dict(sys.modules, sherpa_onnx=module), patch("edge_asr.runtime.package_version", return_value="package-1.31.1"):
            info = runtime_info()
        self.assertEqual(info["sherpa_native_onnxruntime_version"], "native-1.28.2")
        self.assertEqual(info["python_onnxruntime_package_version"], "package-1.31.1")

    def test_default_does_not_import_runtime_and_missing_native_is_null(self):
        with patch.dict(sys.modules, sherpa_onnx=None), patch("edge_asr.runtime.import_module") as importer:
            info = runtime_info()
            importer.assert_not_called()
        self.assertIsNone(info["sherpa_native_onnxruntime_version"])
        self.assertEqual(info["status"], "not_imported")
        with patch.dict(sys.modules, sherpa_onnx=types.SimpleNamespace()), patch("edge_asr.runtime.package_version", return_value="python-only"):
            info = runtime_info()
        self.assertIsNone(info["sherpa_native_onnxruntime_version"])
        self.assertEqual(info["python_onnxruntime_package_version"], "python-only")

    def test_explicit_probe_imports_but_never_constructs_recognizer(self):
        constructor = Mock(side_effect=AssertionError("모델 생성 금지"))
        module = types.SimpleNamespace(onnxruntime_version=lambda: "fixture-native", OfflineRecognizer=constructor)
        with patch.dict(sys.modules, sherpa_onnx=None), patch("edge_asr.runtime.import_module", return_value=module):
            self.assertEqual(runtime_info(probe=True)["sherpa_native_onnxruntime_version"], "fixture-native")
        constructor.assert_not_called()
        with patch.dict(sys.modules, sherpa_onnx=None), patch("edge_asr.runtime.import_module", side_effect=ImportError("fixture missing")):
            info = runtime_info(probe=True)
        self.assertEqual(info["status"], "unavailable")
        self.assertIsNone(info["sherpa_native_onnxruntime_version"])


def snapshot(at, ticks, *, identity=1, extra=False):
    threads = {"20": {"name": "worker", "cpu_ticks": ticks, "start_ticks": 4}}
    if extra:
        threads["21"] = {"name": "new", "cpu_ticks": ticks, "start_ticks": 5}
    return {"at_monotonic_s": at, "process_start_ticks": identity, "threads": threads, "unreadable_threads": 0}


class ThreadDiagnosticTests(unittest.TestCase):
    def test_stat_parser_handles_spaces_and_parentheses_in_thread_name(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            process = proc / "10"
            thread = process / "task/11"
            thread.mkdir(parents=True)
            fields = ["S"] + ["0"] * 19
            fields[11], fields[12], fields[19] = "12", "3", "99"
            text = "10 (worker ) name) " + " ".join(fields)
            (process / "stat").write_text(text)
            (thread / "stat").write_text(text)
            result = thread_snapshot(10, proc)
            self.assertEqual(result["threads"]["11"]["cpu_ticks"], 15)
            self.assertEqual(result["threads"]["11"]["name"], "worker ) name")

    def observe(self, snapshots):
        clock = {"at": 0.0}
        def sleep(seconds):
            clock["at"] += seconds
        with patch("edge_asr.diagnostics.thread_snapshot", side_effect=snapshots), \
                patch("edge_asr.diagnostics.time.monotonic", side_effect=lambda: clock["at"]), \
                patch("edge_asr.diagnostics.time.sleep", side_effect=sleep), \
                patch("edge_asr.diagnostics.os.sysconf", return_value=100):
            return observe_threads(10, 0.2, 0.1)

    def test_cpu_deltas_and_new_thread_boundary_are_distinct(self):
        result = self.observe([snapshot(0, 0), snapshot(0.1, 10, extra=True), snapshot(0.2, 20, extra=True)])
        by_id = {t["tid"]: t for t in result["threads"]}
        self.assertEqual(by_id[20]["observed_cpu_s"], 0.2)
        self.assertEqual(by_id[20]["mean_cpu_percent"], 100)
        self.assertEqual(by_id[21]["observed_cpu_s"], 0.1)
        self.assertIsNone(result["runtime_thread_roles"])

    def test_exit_and_pid_reuse_do_not_mix_cpu_counters(self):
        self.assertEqual(self.observe([snapshot(0, 0), FileNotFoundError()])["status"], "process_exited")
        result = self.observe([snapshot(0, 0), snapshot(0.1, 100, identity=9)])
        self.assertEqual(result["status"], "pid_reused")
        self.assertEqual(result["threads"], [])

    def test_bad_arguments_fail_before_proc_access(self):
        for pid, duration, interval in ((0, 1, 0.1), (True, 1, 0.1), (10, float("nan"), 0.1),
                                         (10, 61, 0.1), (10, 1, 0.001), (10, 1, 2)):
            with self.subTest(pid=pid, duration=duration, interval=interval), \
                    patch("edge_asr.diagnostics.thread_snapshot") as reader, self.assertRaises(ValueError):
                observe_threads(pid, duration, interval)
            reader.assert_not_called()

    @unittest.skipUnless(Path("/proc/self/task").is_dir(), "Linux /proc 전용")
    def test_cli_observes_existing_host_pid_and_protects_output(self):
        command = [sys.executable, str(ROOT / "scripts/edge_asr.py"), "threads", "--pid", str(os.getpid()),
                   "--duration-s", "0.05", "--interval-s", "0.05"]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "diagnosis.json"
            result = subprocess.run([*command, "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(output.read_text())
            self.assertEqual(data["kind"], "existing_pid_thread_observation")
            self.assertGreaterEqual(data["thread_count_observed_max"], 1)
            before = output.read_bytes()
            result = subprocess.run([*command, "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_bytes(), before)
