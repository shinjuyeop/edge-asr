"""과거 냉각 판정 재분석과 읽기 전용 센서 결측 처리를 검증한다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edge_asr.artifacts import digest
from edge_asr.cooling import inventory, live_snapshot, summarize_history


def row(clock, temperature, *, current=0, history=14, fan=None):
    return {
        "at_utc": f"test-{clock}",
        "monotonic_s": clock,
        "temperature_mc": temperature,
        "cpu0_freq_khz": 2400000,
        "current_limits": current,
        "history_limits": history,
        "fan_rpm": fan,
    }


class CoolingTests(unittest.TestCase):
    def test_inventory_missing_fan_is_unavailable_and_multiple_sensors_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(inventory(root)["fan_sensor_status"], "unavailable")
            self.assertIsNone(inventory(root)["device_model"]["value"])
            for index in range(2):
                folder = root / f"class/hwmon/hwmon{index}"
                folder.mkdir(parents=True)
                (folder / "name").write_text(f"fan-{index}")
                (folder / "fan1_input").write_text(str(index * 3000))
            zone = root / "class/thermal/thermal_zone2"
            zone.mkdir(parents=True)
            (zone / "type").write_text("cpu-thermal")
            (zone / "temp").write_text("55000")
            (zone / "trip_point_0_temp").write_text("60000")
            before = {str(p): digest(p) for p in root.rglob("*") if p.is_file()}
            result = inventory(root)
            self.assertEqual([x["rpm"]["value"] for x in result["fans"]], [0, 3000])
            self.assertIsNone(result["fans"][0]["pwm1"]["value"])
            self.assertEqual(
                result["thermal_zones"][0]["trips"][0]["temperature_mc"]["value"], 60000
            )
            self.assertEqual(before, {str(p): digest(p) for p in root.rglob("*") if p.is_file()})

    def history(self, root, trace):
        receipt = {
            "status": "control_rejected",
            "control_valid": False,
            "rejection": "temperature_limit",
            "policy": {"stop_mc": 75000},
            "before": row(100, 52900, fan=3100),
            "after": row(116, 77100, fan=8600),
        }
        (root / "execution.json").write_text(json.dumps(receipt))
        (root / "thermal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in trace))

    def test_software_limit_is_distinct_from_current_throttling_and_old_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.history(root, [row(101, 55000, fan=3200), row(115, 76000, fan=8600)])
            before = {p.name: digest(p) for p in root.iterdir()}
            result = summarize_history(root)
            self.assertEqual(
                result["first_observed_software_temperature_limit"]["elapsed_from_before_s"], 15
            )
            self.assertIsNone(result["first_observed_current_limit"])
            self.assertFalse(result["current_limit_observed"])
            self.assertEqual(result["new_history_bits"], 0)
            self.assertEqual(result["temperature_mc_max_monitor"], 76000)
            self.assertEqual(result["temperature_mc_max_with_before_after"], 77100)
            self.assertEqual(result["fan_positive_samples"], 4)
            self.assertEqual(before, {p.name: digest(p) for p in root.iterdir()})

    def test_current_limit_new_history_and_missing_telemetry_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.history(
                root, [row(101, 60000, current=4, history=15), row(102, None, history=None)]
            )
            result = summarize_history(root)
            self.assertEqual(result["first_observed_current_limit"]["current_limits"], 4)
            self.assertIsNone(result["new_history_bits"])
            self.assertEqual(result["telemetry_missing_samples"], 1)
            self.assertEqual(result["fan_missing_samples"], 2)
            self.history(root, [row(101, 60000, current=4, history=15)])
            self.assertEqual(summarize_history(root)["new_history_bits"], 1)

    def test_bad_history_clock_or_values_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for trace in ([row(99, 55000)], [row(101, -1)], [row(float("nan"), 55000)], []):
                with self.subTest(trace=trace):
                    self.history(root, trace)
                    with self.assertRaises(ValueError):
                        summarize_history(root)

    def test_live_snapshot_identifies_machine_without_starting_load(self):
        with (
            patch("edge_asr.cooling.sample", return_value={"current_limits": None}),
            patch("edge_asr.cooling.inventory", return_value={"fans": []}),
            patch("edge_asr.cooling.platform.machine", return_value="mock-host"),
        ):
            result = live_snapshot()
            self.assertEqual(result["machine"], "mock-host")
            self.assertIsNone(result["telemetry"]["current_limits"])


if __name__ == "__main__":
    unittest.main()
