import unittest

from edge_asr.qwen.viewer.onsets import onset_latencies


class OnsetTests(unittest.TestCase):
    def test_source_and_request_must_share_one_observed_onset(self):
        worker = {
            "audio": {"sample_rate_hz": 100},
            "telemetry": {"t0_monotonic_ns": 100_000_000_000},
            "sources": [{"id": str(i), "start": i * 100, "end": i * 100 + 100} for i in range(3)],
            "segments": [
                {
                    "segment_id": 0,
                    "owned_start_frame": 10,
                    "owned_end_frame": 100,
                    "reason": "hard_limit",
                },
                {
                    "segment_id": 1,
                    "owned_start_frame": 100,
                    "owned_end_frame": 165,
                    "reason": "vad_end",
                },
                {
                    "segment_id": 2,
                    "owned_start_frame": 205,
                    "owned_end_frame": 285,
                    "reason": "vad_end",
                },
            ],
        }
        records = [
            {"type": "active_span", "segment_id": 0, "at_monotonic_s": 100.20},
            {
                "type": "partial_audio_eligible",
                "segment_id": 0,
                "at_monotonic_s": 100.40,
                "start_frame": 10,
                "previous_end_frame": 10,
            },
            {"type": "submitted", "segment_id": 0, "job_id": 7, "kind": "partial"},
            {
                "type": "result",
                "segment_id": 0,
                "job_id": 6,
                "started_monotonic_s": 100.1,
                "ended_monotonic_s": 100.45,
            },
        ]
        first = {
            0: {
                "published_s": 0.81,
                "end_frame": 48,
                "kind": "partial",
                "segment_id": 0,
                "job_id": 7,
                "submitted_monotonic_s": 100.50,
                "started_monotonic_s": 100.51,
            },
            1: {
                "published_s": 1.89,
                "end_frame": 158,
                "kind": "final",
                "segment_id": 1,
                "job_id": 9,
            },
            2: {
                "published_s": 3.20,
                "end_frame": 280,
                "kind": "final",
                "segment_id": 2,
                "job_id": 11,
            },
        }
        result = onset_latencies(worker, records, first, {"0": 12, "1": 169})
        times = result["seconds"]
        self.assertAlmostEqual(times["word_to_first"]["mean"], 0.69)
        self.assertEqual(times["word_to_first"]["n"], 1)
        self.assertEqual(times["file_to_first"]["n"], 3)
        self.assertEqual(times["vad_owned_to_first"]["n"], 2)
        self.assertAlmostEqual(times["eligible_to_submit"]["mean"], 0.1)
        self.assertAlmostEqual(times["worker_busy_before_submit"]["mean"], 0.05)
        self.assertAlmostEqual(times["start_to_first"]["mean"], 0.30)
        self.assertEqual(result["sources"][1]["scope"], "no_source_local_initial_vad_onset")
        self.assertEqual(
            result["sources"][2]["scope"], "no_observed_initial_active_span_before_display"
        )
