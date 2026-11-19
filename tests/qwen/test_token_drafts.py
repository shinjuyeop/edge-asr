"""토큰 후보의 인과성·일회성·실패 수명을 모델 없이 검사한다."""

import unittest
from unittest.mock import Mock

from edge_asr.qwen.token_drafts import FinalTokenDrafts, validate_token_drafts


def job(index=0, kind="partial", **changes):
    return dict(
        job_id=index,
        revision=index,
        kind=kind,
        segment_id=0,
        start_frame=0,
        end_frame=1600,
        available_frame=1600,
        **changes,
    )


class TokenDraftLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.manager = FinalTokenDrafts(
            {"final_token_draft_block": 4, "max_new_tokens": 128},
            {"features": ["token_draft_v1"]},
        )
        self.library = Mock()
        self.library.edge_gguf_set_token_draft.return_value = 0
        self.manager.bind(self.library, 123)

    def candidate(self, status="success", tokens=None):
        self.manager.prepare(job())
        self.manager.begin(1600)
        metadata = {
            "native_result": {
                "schema_version": 1,
                "input_samples": 1600,
                "status": "failed" if status == "cancelled" else status,
                "termination": "callback_error" if status == "cancelled" else "eog",
                "token_ids": [10, 11] if tokens is None else tokens,
            }
        }
        if status == "cancelled":
            metadata["cancellation"] = "final_pending"
        self.manager.finish(metadata, status)

    def supplied(self):
        _, values, count, block = self.library.edge_gguf_set_token_draft.call_args.args
        return list(values)[:count], block

    def test_success_and_cancelled_partial_are_one_call_candidates(self):
        for status in ("success", "cancelled"):
            self.candidate(status)
            self.manager.prepare(job(1, "final"))
            trace = self.manager.begin(1600)
            self.assertEqual(self.supplied(), ([10, 11], 4))
            self.assertEqual(trace["source"]["status"], status)
            self.manager.verify({"token_draft": {"candidate_tokens": 2, "block": 4}})
            self.manager.finish(None, "failed")
            self.manager.prepare(job(2, "final"))
            self.manager.begin(1600)
            self.assertEqual(self.supplied(), ([], 4))

    def test_candidate_requires_same_segment_start_and_earlier_revision(self):
        for changes in (
            {"segment_id": 1},
            {"start_frame": 100},
            {"job_id": 0},
            {"revision": 0},
            {"available_frame": 1500, "end_frame": 1500},
        ):
            self.candidate()
            current = {**job(1, "final"), **changes}
            self.manager.prepare(current)
            self.manager.begin(current["end_frame"] - current["start_frame"])
            self.assertEqual(self.supplied()[0], [])
            self.manager.finish(None, "failed")
        # 이미 도착했던 부분 음성은 VAD가 확정 종료점을 과거로 보정해도 인과적이다.
        self.candidate()
        self.manager.prepare({**job(1, "final"), "end_frame": 1200})
        self.manager.begin(1200)
        self.assertEqual(self.supplied()[0], [10, 11])

    def test_next_partial_and_unprepared_batch_never_use_candidates(self):
        self.candidate()
        self.manager.prepare(job(1))
        self.manager.begin(1600)
        self.assertEqual(self.supplied(), ([], 1))
        self.manager.finish(None, "failed")
        self.manager.prepare(job(2, "final"))
        self.manager.begin(1600)
        self.assertEqual(self.supplied()[0], [])
        self.candidate()
        self.manager.begin(1600)
        self.assertEqual(self.supplied(), ([], 1))
        self.manager.finish(None, "success")
        self.assertIsNone(self.manager.previous)

    def test_failed_invalid_or_oversized_tokens_are_not_saved(self):
        for tokens in ([], [-1], [True], [2**31], [1.5], [1] * 129):
            self.candidate(tokens=tokens)
            self.assertIsNone(self.manager.previous)
        self.candidate("failed")
        self.assertIsNone(self.manager.previous)
        self.candidate()
        self.manager.prepare(job(1))
        with self.assertRaises(ValueError):
            self.manager.begin(1599)
        self.manager.finish(None, "failed")
        self.assertIsNone(self.manager.previous)

    def test_job_is_copied_native_mismatch_and_bad_configuration_fail(self):
        request = job()
        self.manager.prepare(request)
        request["segment_id"] = 100
        self.manager.begin(1600)
        self.assertEqual(self.manager.active["segment_id"], 0)
        with self.assertRaises(ValueError):
            self.manager.verify({"token_draft": {"candidate_tokens": 2, "block": 4}})
        for value in (0, True, 3, 4.0, None):
            with self.assertRaises(ValueError):
                validate_token_drafts({"final_token_draft_block": value})
        with self.assertRaises(ValueError):
            FinalTokenDrafts({"final_token_draft_block": 4}, {})
        for changes in ({"revision": -1}, {"kind": "batch"}, {"end_frame": 1700}):
            with self.assertRaises(ValueError):
                self.manager.prepare({**job(), **changes})
        self.candidate()
        self.manager.close()
        self.assertIsNone(self.manager.previous)
        self.assertIsNone(self.manager.handle)
