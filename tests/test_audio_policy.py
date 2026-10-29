import hashlib
import math
import random
import unittest
from array import array

from edge_asr.audio_policy import AudioPreprocessor, preprocess
from edge_asr.thermal import rejection


class AudioPolicyTests(unittest.TestCase):
    def test_block_state_matches_frozen_m30_golden_outputs(self):
        rng = random.Random(730)
        audio = array(
            "f",
            [0.0] * 1600
            + [rng.uniform(-0.003, 0.003) for _ in range(1700)]
            + [rng.uniform(-0.8, 0.8) for _ in range(1700)]
            + [0.0] * 1600,
        )
        # df3a7fb의 기존 preprocess에서 얻은 SHA-256. 새 구현으로 기대값을 만들지 않는다.
        expected = {
            "identity": "529d9797914611d21d4755ab8d58b6e3e5c196917f8e3a26d5fc39935486e10f",
            "gain12": "e9cf46d5892108ddf8fe7acfc14e5c08e29f41738c627fa52d038c23f647c9ac",
            "rms26": "65269dbcf18bc5ff599224e92f06b13dea7cafd793d8bab5486d0f2d25a046f5",
        }
        for policy, sha in expected.items():
            processor = AudioPreprocessor(policy)
            output = array("h")
            for start in range(0, len(audio), 1600):
                block, _ = processor.process_block(
                    audio[start : start + 1600], final=start + 1600 >= len(audio)
                )
                output.extend(block)
            self.assertEqual(hashlib.sha256(output.tobytes()).hexdigest(), sha)
            self.assertEqual(
                hashlib.sha256(preprocess(audio, policy)[0].tobytes()).hexdigest(), sha
            )

    def test_state_survives_blocks_and_reset_is_explicit(self):
        audio = array("f", [0.002, -0.002] * 800)
        processor = AudioPreprocessor("rms26")
        first, a = processor.process_block(audio)
        _, b = processor.process_block(audio)
        self.assertGreater(b["gain"], a["gain"])
        with self.assertRaises(ValueError):
            processor.process_block(audio[:3])
        processor.process_block(audio[:3], final=True)
        with self.assertRaises(ValueError):
            processor.process_block(audio)
        processor.reset()
        reset, trace = processor.process_block(audio)
        self.assertEqual(first, reset)
        self.assertEqual(trace["start_frame"], 0)

    def test_identity_is_exact_for_pcm16(self):
        integers = [-32768, -100, 0, 100, 32767]
        output, _ = preprocess(array("f", (x / 32768 for x in integers)), "identity")
        self.assertEqual(list(output), integers)

    def test_fixed_gain_reports_clipping_and_rms_caps_peaks(self):
        audio = array("f", [0.5, -0.5, 0.0001, -0.0001] * 800)
        fixed, f = preprocess(audio, "gain12")
        normalized, n = preprocess(audio, "rms26")
        self.assertGreater(f["newly_clipped_samples"], 0)
        self.assertEqual(n["newly_clipped_samples"], 0)
        self.assertLessEqual(max(abs(x) for x in normalized) / 32768, 0.98 + 1 / 32768)
        self.assertEqual(len(fixed), len(audio))

    def test_rms_is_causal_at_block_boundary_and_boost_is_bounded(self):
        prefix = array("f", (0.002 * math.sin(i / 15) for i in range(3200)))
        a, meta = preprocess(prefix, "rms26")
        b, _ = preprocess(prefix + array("f", [0.9] * 4000), "rms26")
        self.assertEqual(a, b[: len(a)])
        self.assertTrue(all(1 <= r["gain"] <= 10 ** (24 / 20) for r in meta["gain_trace"]))

    def test_silence_is_preserved_and_invalid_input_rejected(self):
        for policy in ("identity", "gain12", "rms26"):
            samples, meta = preprocess(array("f", [0] * 1733), policy)
            self.assertFalse(any(samples))
            self.assertIsNone(meta["output_rms_dbfs"])
        for values in ([], [float("nan")], [2.0]):
            with self.assertRaises(ValueError):
                preprocess(array("f", values), "identity")


class ThermalTests(unittest.TestCase):
    def test_old_history_does_not_mean_current_throttling(self):
        row = {
            "temperature_mc": 53000,
            "cpu0_freq_khz": 2400000,
            "current_limits": 0,
            "history_limits": 14,
        }
        self.assertIsNone(rejection(row, 14))
        self.assertEqual(
            rejection({**row, "current_limits": 4}, 14), "current_throttling_or_voltage_limit"
        )
        self.assertEqual(rejection({**row, "temperature_mc": 75000}, 14), "temperature_limit")
        self.assertEqual(rejection({**row, "cpu0_freq_khz": None}, 14), "telemetry_unavailable")
        self.assertEqual(rejection(row, 8), "new_limit_history_since_admission")


if __name__ == "__main__":
    unittest.main()
