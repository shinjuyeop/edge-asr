"""문자 철회와 자기 최종 전사 일치를 정답 정확도와 구분한다."""

import unittest

from edge_asr.qwen.realtime.stability import replays_prefix, summarize


def event(text, at, *, job=0, segment=0, kind="partial", token=False, status="success"):
    return {
        "type": "token" if token else "result",
        "job_id": job,
        "segment_id": segment,
        "kind": kind,
        "hypothesis": text,
        "published_s": at,
        "status": "provisional" if token else status,
        "displayed": True,
    }


class CaptionStabilityTests(unittest.TestCase):
    def test_only_same_segment_other_call_matching_raw_prefix_is_replay(self):
        old = event("한국어 문장", 1)
        self.assertTrue(replays_prefix(old, event("한국어", 2, job=1, token=True)))
        self.assertTrue(replays_prefix(old, event("한국어 문장", 2, job=1, token=True)))
        for row in (
            event("", 2, job=1),
            event("다른 문장", 2, job=1),
            event("한국어문장", 2, job=1),
            event("한국어", 2, job=0),
            event("한국어", 2, job=1, segment=1),
        ):
            self.assertFalse(replays_prefix(old, row))
        self.assertFalse(replays_prefix(None, old))

    def test_replayed_prefix_retracts_three_characters_and_delays_retention(self):
        rows = [
            event("가나다라", 1),
            event("가", 2, job=1, kind="final", token=True),
            event("가나다라", 3, job=1, kind="final", token=True),
            event("가나다라마", 4, job=1, kind="final"),
        ]
        result = summarize(rows)
        self.assertEqual(result["normalized_revisions"], 1)
        self.assertEqual(result["retracted_suffix_characters"], 3)
        self.assertEqual(result["prefix_replay_revisions"], 1)
        self.assertEqual(result["within_call_revisions"], 0)
        self.assertEqual(result["first4_to_final_consistent4_s"]["mean"], 2)
        self.assertEqual(
            result["latest_completed_partial_vs_final"]["whole_partial_is_final_prefix"], 1
        )
        rows[1].update(displayed=False, prefix_replay_suppressed=True)
        fixed = summarize(rows)
        self.assertEqual(fixed["normalized_revisions"], 0)
        self.assertEqual(fixed["suppressed_prefix_jobs"], 1)
        self.assertEqual(fixed["first4_to_final_consistent4_s"]["mean"], 0)

    def test_true_correction_and_final_failure_remain_in_revision_counts(self):
        rows = [
            event("가나다라", 1),
            event("가나마", 2, job=1, kind="final", token=True),
            event("가나마바사", 3, job=1, kind="final"),
        ]
        result = summarize(rows)
        self.assertEqual(result["prefix_replay_revisions"], 0)
        self.assertEqual(result["retracted_suffix_characters"], 2)
        self.assertEqual(
            result["latest_completed_partial_vs_final"]["retained_prefix_characters"], 2
        )
        rows[-1] = event(None, 3, job=1, kind="final", status="failed")
        failed = summarize(rows)
        self.assertEqual(failed["failed_results"], 1)
        self.assertEqual(failed["retracted_suffix_characters"], 5)
        self.assertEqual(failed["first4_to_final_consistent4_s"]["n"], 0)
        self.assertEqual(failed["latest_completed_partial_vs_final"]["paired_segments"], 0)

    def test_normalization_empty_short_final_and_time_reversal(self):
        empty = summarize([])
        self.assertIsNone(empty["latest_completed_partial_vs_final"]["retained_prefix_fraction"])
        rows = [event("가 나!", 1), event("가나", 2, job=1, kind="final")]
        result = summarize(rows)
        self.assertEqual(result["normalized_revisions"], 0)
        self.assertEqual(result["finals_shorter_than4"], 1)
        rows[-1]["published_s"] = 0
        with self.assertRaisesRegex(ValueError, "역행"):
            summarize(rows)


if __name__ == "__main__":
    unittest.main()
