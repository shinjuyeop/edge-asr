"""과거 75°C 정책과 새 경고/중단 경계·실제 제한 우선·외부 제한 확인을 모의 검사한다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr import thermal
from edge_asr.thermal import policy_settings, rejection, temperature_warning
from edge_asr.tools.probes import thermal as guard

COOL = {"temperature_mc": 52000, "cpu0_freq_khz": 2400000, "current_limits": 0, "history_limits": 0}
LIMITS = "Max address space         4294967296           4294967296           bytes\n"


class ThermalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.git = patch.object(guard.subprocess, "check_output", return_value="fixture\n")
        self.git.start()
        self.addCleanup(self.git.stop)

    def test_legacy_default_and_new_boundaries_are_distinct(self):
        self.assertEqual(policy_settings(), {"warn_mc": None, "stop_mc": 75000, "interval_s": 1})
        new = policy_settings("warn75-stop80")
        self.assertFalse(temperature_warning({**COOL, "temperature_mc": 74999}, new["warn_mc"]))
        self.assertTrue(temperature_warning({**COOL, "temperature_mc": 75000}, new["warn_mc"]))
        self.assertFalse(temperature_warning({}, new["warn_mc"]))
        self.assertEqual(rejection({**COOL, "temperature_mc": 75000}), "temperature_limit")
        self.assertIsNone(
            rejection({**COOL, "temperature_mc": 79999}, max_temperature_mc=new["stop_mc"])
        )
        self.assertEqual(
            rejection({**COOL, "temperature_mc": 80000}, max_temperature_mc=new["stop_mc"]),
            "temperature_limit",
        )
        new["stop_mc"] = 99000
        self.assertEqual(policy_settings("warn75-stop80")["stop_mc"], 80000)

    def test_unknown_policy_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            with self.assertRaises(ValueError):
                guard.run(Path(directory), out, ["false"], temperature_policy="stop100")
            self.assertFalse(out.exists())

    def test_actual_arm_clock_is_optional_and_does_not_replace_sysfs_frequency(self):
        for output, expected in (("frequency(0)=1500000000", 1500000000), ("bad", None)):
            with (
                self.subTest(output=output),
                patch.object(
                    thermal.subprocess,
                    "run",
                    side_effect=[
                        Mock(returncode=0, stdout="throttled=0x40004"),
                        Mock(returncode=0, stdout=output),
                    ],
                ),
                patch.object(Path, "read_text", return_value="2400000"),
            ):
                row = thermal.sample(measure_arm_clock=True)
                self.assertEqual(row["arm_clock_hz"], expected)
                self.assertEqual(row["cpu0_freq_khz"], 2400000)
                self.assertEqual(row["current_limits"], 4)
                self.assertEqual(row["history_limits"], 4)

    def test_operational_allows_thermal_limits_but_not_voltage_or_missing_data(self):
        settings = policy_settings("operational")
        self.assertIsNone(settings["stop_mc"])
        hot = {**COOL, "temperature_mc": 86000, "current_limits": 14, "history_limits": 14}
        self.assertTrue(temperature_warning(hot, settings["warn_mc"]))
        for row, expected in (
            (hot, None),
            ({**hot, "current_limits": 15}, "current_undervoltage"),
            ({**hot, "history_limits": 15}, "new_undervoltage_history"),
            ({**hot, "cpu0_freq_khz": None}, "telemetry_unavailable"),
        ):
            with self.subTest(row=row):
                self.assertEqual(
                    rejection(row, max_temperature_mc=None, allow_thermal_limits=True), expected
                )
        self.assertIsNone(
            rejection(
                {**hot, "history_limits": 15},
                initial_history=1,
                max_temperature_mc=None,
                allow_thermal_limits=True,
            )
        )

    def test_operational_unverified_limits_do_not_pass_even_when_child_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "run"
            child = Mock()
            child.poll.return_value = 0
            child.wait.return_value = 0
            with (
                patch.object(guard, "admit", return_value=COOL),
                patch.object(guard, "sample", return_value=COOL),
                patch.object(guard.subprocess, "Popen", return_value=child),
                patch.object(guard, "terminate"),
            ):
                self.assertEqual(
                    guard.run(root, out, ["mock"], temperature_policy="operational"), 1
                )
            receipt = json.loads((out / "execution.json").read_text())
            self.assertEqual(receipt["status"], "failed_or_unverified")
            self.assertFalse(receipt["control_valid"])
            self.assertFalse(receipt["operational_valid"])

    def test_warning_allows_completion_only_with_verified_process_limits(self):
        for policy in ("warn75-stop80", "operational"):
            with self.subTest(policy=policy):
                self.check_warning_completion(policy)

    def check_warning_completion(self, policy):
        operational = policy == "operational"
        observed = (
            {**COOL, "temperature_mc": 86000, "current_limits": 14, "history_limits": 14}
            if operational
            else {**COOL, "temperature_mc": 76550}
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "run"
            child = Mock()
            child.poll.side_effect = [None, 0]
            child.wait.return_value = 0
            original_read = Path.read_text

            def read(path, *args, **kwargs):
                return (
                    LIMITS
                    if str(path) == "/proc/98765/limits"
                    else original_read(path, *args, **kwargs)
                )

            def launch(*args, **kwargs):
                (out / "worker.json").write_text(json.dumps({"process_id": 98765}))
                return child

            with (
                patch.object(guard, "admit", return_value=COOL),
                patch.object(guard, "sample", side_effect=[observed, COOL]),
                patch.object(guard.subprocess, "Popen", side_effect=launch),
                patch.object(guard, "terminate"),
                patch.object(guard, "thread_snapshot", return_value={"threads": {}}),
                patch.object(guard.time, "sleep") as sleep,
                patch.object(Path, "read_text", read),
            ):
                self.assertEqual(guard.run(root, out, ["mock"], temperature_policy=policy), 0)
            receipt = json.loads((out / "execution.json").read_text())
            warnings = [json.loads(x) for x in (out / "warnings.jsonl").read_text().splitlines()]
            self.assertEqual(receipt["control_valid"], not operational)
            if operational:
                self.assertTrue(receipt["operational_valid"])
                self.assertEqual(receipt["evaluation_scope"], "operational_sustained_load")
                self.assertTrue(receipt["policy"]["allow_thermal_limits"])
                self.assertEqual(receipt["policy"]["firmware_thermal_protection"], "unchanged")
            self.assertTrue(receipt["limit_verified"])
            self.assertEqual(receipt["warnings"]["sample_count"], 1)
            self.assertEqual(receipt["warnings"]["first_sample"], observed)
            self.assertEqual(warnings[0]["phase"], "monitor")
            self.assertEqual(receipt["policy"]["name"], policy)
            self.assertEqual(receipt["policy"]["stop_mc"], None if operational else 80000)
            sleep.assert_called_once_with(1 if operational else 0.25)

    def test_80_degrees_current_history_and_missing_telemetry_always_stop(self):
        samples = [
            ({**COOL, "temperature_mc": 80000}, "temperature_limit"),
            ({**COOL, "current_limits": 4}, "current_throttling_or_voltage_limit"),
            ({**COOL, "history_limits": 1}, "new_limit_history_since_admission"),
            ({**COOL, "cpu0_freq_khz": None}, "telemetry_unavailable"),
        ]
        for row, reason in samples:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                out = root / "run"
                child = Mock()
                child.poll.return_value = None
                child.wait.return_value = -15
                with (
                    patch.object(guard, "admit", return_value=COOL),
                    patch.object(guard, "sample", return_value=row),
                    patch.object(guard.subprocess, "Popen", return_value=child),
                    patch.object(guard, "terminate") as terminate,
                ):
                    self.assertEqual(
                        guard.run(root, out, ["mock"], temperature_policy="warn75-stop80"), 1
                    )
                receipt = json.loads((out / "execution.json").read_text())
                self.assertEqual(receipt["status"], "control_rejected")
                self.assertEqual(receipt["rejection"], reason)
                self.assertFalse(receipt["control_valid"])
                self.assertTrue(terminate.called)

    def test_final_after_sample_can_reject_completed_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "run"
            child = Mock()
            child.poll.return_value = 0
            child.wait.return_value = 0
            with (
                patch.object(guard, "admit", return_value=COOL),
                patch.object(guard, "sample", return_value={**COOL, "temperature_mc": 80000}),
                patch.object(guard.subprocess, "Popen", return_value=child),
                patch.object(guard, "terminate"),
            ):
                self.assertEqual(
                    guard.run(root, out, ["mock"], temperature_policy="warn75-stop80"), 1
                )
            receipt = json.loads((out / "execution.json").read_text())
            self.assertEqual(receipt["rejection"], "temperature_limit")
            self.assertEqual(json.loads((out / "warnings.jsonl").read_text())["phase"], "after")


if __name__ == "__main__":
    unittest.main()
