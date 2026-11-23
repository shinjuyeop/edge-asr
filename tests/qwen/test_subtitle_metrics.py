import unittest

from edge_asr.qwen.viewer.continuity import continuity


class SubtitleMetricsTests(unittest.TestCase):
    def test_new_audio_and_new_text_must_coincide(self):
        def event(job, end, at, text, **more):
            return dict(
                type="token",
                job_id=job,
                kind="partial",
                status="provisional",
                segment_id=0,
                start_frame=0,
                end_frame=end,
                hypothesis=text,
                displayed=True,
                published_s=at,
                **more,
            )

        worker = {
            "sources": [
                {"id": "a", "start": 0, "end": 500},
                {"id": "b", "start": 500, "end": 1000},
            ],
            "audio": {"frames": 1000, "sample_rate_hz": 100},
        }
        events = [
            event(0, 100, 1.1, "!"),
            event(0, 100, 1.2, "한국"),
            event(0, 100, 1.3, "한국어"),
            event(1, 200, 2.2, "한"),
            event(1, 200, 2.3, "한국어"),
            event(1, 200, 2.4, "한국어로"),
            event(2, 300, 3.2, "낡은 표시", stale_revision=True),
            event(3, 700, 7.2, "두 원음에 걸침"),
        ]
        events.append(
            {
                **events[-1],
                "kind": "final",
                "status": "success",
                "type": "result",
                "displayed": False,
                "published_s": 7.3,
            }
        )
        result = continuity(worker, events)
        self.assertEqual(result["raw_first_any_s"]["mean"], 1.1)
        self.assertEqual(result["meaningful_first_s"]["mean"], 1.2)
        self.assertEqual(result["meaningful_four_s"]["mean"], 2.4)
        self.assertEqual(result["fresh_count"], 3)
        self.assertEqual(result["missing_first"], 1)
        self.assertEqual(result["missing_file_final"], 0)
        self.assertAlmostEqual(result["common_file_end_final_s"]["p95"], 2.3)

    def test_left_context_cannot_delay_the_previous_files_final(self):
        worker = {
            "sources": [
                {"id": "a", "start": 0, "end": 100},
                {"id": "b", "start": 100, "end": 200},
            ],
            "audio": {"frames": 200, "sample_rate_hz": 100},
        }
        final = dict(type="result", kind="final", status="success", displayed=True)
        a = dict(
            final,
            job_id=1,
            segment_id=0,
            start_frame=0,
            end_frame=100,
            hypothesis="앞",
            published_s=1.2,
        )
        b = dict(
            final,
            job_id=2,
            segment_id=1,
            start_frame=80,
            owned_start_frame=100,
            end_frame=200,
            hypothesis="뒤",
            published_s=2.4,
        )
        report = continuity(worker, [a, b])
        self.assertAlmostEqual(report["common_file_end_final_s"]["mean"], 0.3)
