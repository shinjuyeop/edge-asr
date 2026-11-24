import unittest

from edge_asr.metrics import character_errors
from edge_asr.qwen.viewer.boundary_errors import boundary_errors, edit_coordinates, punctuation


def worker(reference, left, right):
    return {
        "status": "completed",
        "final_transcript_score": character_errors(reference, left + right),
        "sources": [
            {"id": "src", "reference": reference, "start": 0, "end": 20, "audio_sha256": "pcm"}
        ],
        "results": [
            {
                "segment_id": i,
                "kind": "final",
                "status": "success",
                "hypothesis": text,
                "start_frame": i * 10,
                "end_frame": (i + 1) * 10,
            }
            for i, text in enumerate((left, right))
        ],
    }


class BoundaryErrorTests(unittest.TestCase):
    def test_inserted_prefix_and_official_punctuation(self):
        sample = worker("공원에는 매일", "공원에는 매.", "매일.")
        annotation = {"id": "src", "reference": "공원에는 매일.", "audio_sha256": "pcm"}
        report = boundary_errors(sample, [annotation])
        self.assertEqual(report["inserted_copies"], 1)
        self.assertEqual(report["adjacent_repetitions"][0]["text"], "매")
        self.assertEqual(
            report["punctuation"]["sentence"],
            {"reference": 1, "predicted": 2, "matched": 1, "extra": 1, "missing": 0},
        )
        self.assertEqual(list(punctuation("3.14초, 1:30.")), [(4, "clause"), (7, "sentence")])

    def test_real_repetition_and_mismatched_annotations(self):
        sample = worker("안 들어요 안 들어요", "안 들어요.", "안 들어요.")
        self.assertEqual(boundary_errors(sample)["inserted_copies"], 0)
        for annotation in [
            {"id": "src", "reference": "안 들어요 안 들어요", "audio_sha256": "other"},
            {"id": "src", "reference": "다 른 본 문", "audio_sha256": "pcm"},
        ]:
            with self.assertRaises(ValueError):
                boundary_errors(sample, [annotation])
        sample["status"] = "failed"
        with self.assertRaises(ValueError):
            boundary_errors(sample)

    def test_alignment_preserves_right_edge_of_deleted_text(self):
        self.assertEqual(edit_coordinates("abcd", "axd"), ([0, 2, 3, 4], set(), 2))
        self.assertEqual(edit_coordinates("", "x"), ([0, 0], {0}, 1))
        self.assertEqual(edit_coordinates("xy", ""), ([2], set(), 2))
