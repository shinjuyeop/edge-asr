from array import array
import math
import unittest

from edge_asr.audio_policy import preprocess
from edge_asr.thermal import rejection


class AudioPolicyTests(unittest.TestCase):
    def test_identity_is_exact_for_pcm16(self):
        integers = [-32768, -100, 0, 100, 32767]
        output, _ = preprocess(array("f", (x / 32768 for x in integers)), "identity")
        self.assertEqual(list(output), integers)

    def test_fixed_gain_reports_clipping_and_rms_caps_peaks(self):
        audio = array("f", [.5, -.5, .0001, -.0001] * 800)
        fixed, f = preprocess(audio, "gain12")
        normalized, n = preprocess(audio, "rms26")
        self.assertGreater(f["newly_clipped_samples"], 0)
        self.assertEqual(n["newly_clipped_samples"], 0)
        self.assertLessEqual(max(abs(x) for x in normalized) / 32768, .98 + 1 / 32768)
        self.assertEqual(len(fixed), len(audio))

    def test_rms_is_causal_at_block_boundary_and_boost_is_bounded(self):
        prefix = array("f", (.002 * math.sin(i / 15) for i in range(3200)))
        a, meta = preprocess(prefix, "rms26")
        b, _ = preprocess(prefix + array("f", [.9] * 4000), "rms26")
        self.assertEqual(a, b[:len(a)])
        self.assertTrue(all(1 <= r["gain"] <= 10**(24/20) for r in meta["gain_trace"]))

    def test_silence_is_preserved_and_invalid_input_rejected(self):
        for policy in ("identity", "gain12", "rms26"):
            samples, meta = preprocess(array("f", [0] * 1733), policy)
            self.assertFalse(any(samples)); self.assertIsNone(meta["output_rms_dbfs"])
        for values in ([], [float("nan")], [2.0]):
            with self.assertRaises(ValueError): preprocess(array("f", values), "identity")


class ThermalTests(unittest.TestCase):
    def test_old_history_does_not_mean_current_throttling(self):
        row = {"temperature_mc": 53000, "cpu0_freq_khz": 2400000, "current_limits": 0, "history_limits": 14}
        self.assertIsNone(rejection(row, 14))
        self.assertEqual(rejection({**row, "current_limits": 4}, 14), "current_throttling_or_voltage_limit")
        self.assertEqual(rejection({**row, "temperature_mc": 75000}, 14), "temperature_limit")
        self.assertEqual(rejection({**row, "cpu0_freq_khz": None}, 14), "telemetry_unavailable")
        self.assertEqual(rejection(row, 8), "new_limit_history_since_admission")


if __name__ == "__main__": unittest.main()
