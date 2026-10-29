"""실제 모델·RP5 없이 파생 자료 보존 및 통제 실패 전파를 검사한다."""
from array import array
import importlib.util
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


guard = script("m30_thermal_run")
data = script("prepare_m30_audio")
report = script("summarize_m30")
COOL = {"temperature_mc": 52000, "current_limits": 0, "history_limits": 14, "cpu0_freq_khz": 2400000}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.git = patch.object(guard.subprocess, "check_output", return_value="fixture-commit\n")
        self.git.start(); self.addCleanup(self.git.stop)

    def test_admission_uses_three_consecutive_samples_and_returns_that_sample(self):
        with tempfile.TemporaryDirectory() as d, patch.object(guard, "sample", side_effect=[COOL, {**COOL, "temperature_mc": 54000}, COOL, COOL, COOL]), patch.object(guard.time, "sleep"):
            self.assertEqual(guard.admit(Path(d)), COOL)
            self.assertEqual(len((Path(d) / "admission.jsonl").read_text().splitlines()), 5)

    def test_missing_telemetry_never_starts_inference(self):
        with tempfile.TemporaryDirectory() as d, patch.object(guard, "sample", return_value={}), patch.object(guard.subprocess, "Popen") as popen:
            output = Path(d) / "run"
            self.assertEqual(guard.run(ROOT, output, ["false"]), 1)
            popen.assert_not_called()
            self.assertEqual(json.loads((output / "execution.json").read_text())["status"], "blocked")

    def test_thermal_rejection_terminates_process_and_is_not_success(self):
        child = Mock(); child.poll.side_effect = [None, 0]; child.wait.return_value = -15
        hot = {**COOL, "temperature_mc": 75000}
        with tempfile.TemporaryDirectory() as d, patch.object(guard, "admit", return_value=COOL), patch.object(guard, "sample", return_value=hot), patch.object(guard.subprocess, "Popen", return_value=child), patch.object(guard, "terminate") as stop:
            output = Path(d) / "run"
            self.assertEqual(guard.run(ROOT, output, ["command"]), 1)
            self.assertTrue(stop.called)
            record = json.loads((output / "execution.json").read_text())
            self.assertEqual(record["status"], "control_rejected")
            self.assertFalse(record["control_valid"])

    def test_exit_zero_without_verified_limits_is_unverified(self):
        child = Mock(); child.poll.return_value = 0; child.wait.return_value = 0
        with tempfile.TemporaryDirectory() as d, patch.object(guard, "admit", return_value=COOL), patch.object(guard, "sample", return_value=COOL), patch.object(guard.subprocess, "Popen", return_value=child):
            output = Path(d) / "run"
            self.assertEqual(guard.run(ROOT, output, ["command"]), 1)
            self.assertEqual(json.loads((output / "execution.json").read_text())["status"], "failed_or_unverified")

    def test_preparation_preserves_source_has_same_references_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / "data/evaluation/kss-dev50"; source.mkdir(parents=True)
            wav = source / "source.wav"; data.write_wav(wav, array("h", [1000, -1000] * 2000))
            before = wav.read_bytes()
            manifest = source / "manifest.jsonl"
            manifest.write_text(json.dumps({"id": "sample", "audio_path": str(wav.relative_to(root)), "reference": "안녕하세요", "audio_sha256": data.digest(wav)}))
            output = root / "data/evaluation/fixture"
            result = data.prepare(root, output)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(before, wav.read_bytes())
            for policy in data.POLICIES:
                rows, _ = data.load_manifest(output / f"dev-{policy}.jsonl", root)
                self.assertEqual(len(rows), 3)
                self.assertEqual({r.reference for r in rows}, {"안녕하세요"})
                for row in rows: self.assertEqual(data.digest(row.audio_path), row.audio_sha256)
            with self.assertRaises(ValueError): data.prepare(root, output)

    def test_common_selection_rejects_clipping_clean_regression_and_missing_results(self):
        fixture = {p: {m: {c: {"complete": True, "successful": 50, "cer": {"value": .1}} for c in ("clean", "quiet24", "quiet24_noise55")} for m in ("zipformer", "sensevoice")} for p in ("identity", "gain12", "rms26")}
        clip = {"identity": 0, "gain12": 1, "rms26": 0}
        self.assertEqual(report.select_policy(fixture, clip)["selected_common_policy"], "identity")
        fixture["rms26"]["zipformer"]["quiet24"]["cer"]["value"] = .01
        self.assertEqual(report.select_policy(fixture, clip)["selected_common_policy"], "rms26")
        fixture["rms26"]["sensevoice"]["clean"]["cer"]["value"] = .12
        self.assertEqual(report.select_policy(fixture, clip)["selected_common_policy"], "identity")
        for mutation in (lambda f: f.pop("gain12"), lambda f: f["identity"]["zipformer"]["clean"].update(complete=False)):
            bad = deepcopy(fixture); mutation(bad)
            with self.assertRaises(ValueError): report.select_policy(bad, clip)


if __name__ == "__main__": unittest.main()
