"""실제 모델 없이 소유 PCM 검증·지속 활성·native 예산 실패 기록을 검사한다."""

import unittest
from array import array

from edge_asr.dataset import Audio
from edge_asr.legacy.tools.probes.vad_duration import measure, probe
from edge_asr.vad_probability import BoundedProbabilityVad


class Probability:
    def reset(self):
        self.resets = getattr(self, "resets", 0) + 1

    def __call__(self, samples):
        return 1.0


class DurationTests(unittest.TestCase):
    def test_unsupported_backend_rejected_before_assets(self):
        with self.assertRaisesRegex(ValueError, "백엔드"):
            probe(None, None, None, None, "unknown")

    def test_long_pcm_all_caps_keep_ownership_and_one_reset_per_session(self):
        audio = Audio(array("f", [0.02] * (36 * 16000 + 137)), 16000, "fixture")
        for maximum in (3, 5, 8):
            probability = Probability()
            vad = BoundedProbabilityVad(probability)
            before = probability.resets
            result = measure(vad, audio, "identity", maximum, bounded=True)
            self.assertEqual(result["status"], "completed", result["error"])
            self.assertTrue(result["over_30s_active_observed"])
            self.assertFalse(result["native_guard_observed"])
            self.assertEqual(probability.resets - before, 1)
            self.assertEqual(
                sum(e["owned_end_frame"] - e["owned_start_frame"] for e in result["segments"]),
                len(audio.samples),
            )
            self.assertLessEqual(
                result["pipeline"]["ring_peak_frames"], result["pipeline"]["ring_capacity_frames"]
            )

    def test_native_budget_failure_is_preserved_and_does_not_claim_completed(self):
        audio = Audio(array("f", [0.02] * 36 * 16000), 16000, "fixture")
        result = measure(BoundedProbabilityVad(Probability()), audio, "identity", 5, bounded=False)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["native_guard_observed"])
        self.assertFalse(result["pipeline"]["complete"])
        self.assertFalse(result["over_30s_active_observed"])


if __name__ == "__main__":
    unittest.main()
