"""긴 발화의 불완전 전사 제외·화자 분리·원음 절취 계약. 모델/네트워크 없음."""

import random
import tempfile
import unittest
from pathlib import Path

from edge_asr.dataset import write_wav
from edge_asr.textgrid import Interval
from edge_asr.tools.data import longform


class LongformTests(unittest.TestCase):
    def test_interviewer_unknown_and_private_intervals_cannot_be_crossed(self):
        for marker in ("<IVER>", "<UNKNOWN>", "<PRIVATE.INFO>", "<LAUGH-네>"):
            rows = [
                Interval(0, 30, "앞 문장"),
                Interval(30, 31, marker),
                Interval(31, 65, "뒤 문장"),
            ]
            self.assertEqual(list(longform.natural_candidates("s01m16f1.TextGrid", rows)), [])

    def test_natural_clip_preserves_full_utterances_fillers_and_internal_silence(self):
        rows = [
            Interval(0, 20, "어 저는"),
            Interval(20, 22, "<SIL>"),
            Interval(22, 30, "그 음"),
            Interval(30, 31, "<LAUGH>"),
            Interval(31, 60, "계속 이야기합니다"),
            Interval(60, 61, "<IVER>"),
        ]
        selected = list(longform.natural_candidates("s01m16f1.TextGrid", rows))
        self.assertEqual(len(selected), 1)
        row = selected[0]
        self.assertEqual((row["source_start_s"], row["source_end_s"]), (0, 60))
        self.assertEqual(row["reference"], "어 저는 그 음 계속 이야기합니다")
        self.assertEqual(row["label_metrics"]["silence_s"], 2)
        self.assertEqual(row["label_metrics"]["filled_pause_tokens"], 3)
        self.assertEqual(row["label_metrics"]["laughter_s"], 1)

    def test_style_selection_is_deterministic_and_missing_quota_fails(self):
        rows = []
        for i, (age, gender, _) in enumerate(longform.QUOTAS):
            for variant in range(2):
                rows.append(
                    {
                        "id": f"{i}-{variant}",
                        "age_group": age,
                        "gender": gender,
                        "label_metrics": {
                            "duration_s": 60,
                            "speech_ratio": 0.8,
                            "long_pauses": 2,
                            "filled_pause_tokens": 3,
                            "laughter_s": 1,
                        },
                    }
                )
        first = longform.select_natural(rows)
        random.Random(17).shuffle(rows)
        self.assertEqual(first, longform.select_natural(rows))
        with self.assertRaisesRegex(ValueError, "할당량"):
            longform.select_natural([r for r in rows if r["age_group"] != 10])

    def test_reading_excludes_old_references_and_uses_four_distinct_speakers(self):
        rows = [
            {
                "id": f"{gender}-{speaker}-{i}",
                "speaker_id": f"{gender}-{speaker}",
                "gender": gender,
                "frames": 16000 * 10,
                "reference": f"{gender} {speaker} 문장 {i}",
            }
            for gender in ("male", "female")
            for speaker in range(3)
            for i in range(9)
        ]
        excluded = {longform.normalize(rows[0]["reference"])}
        selected = longform.select_read(rows, excluded)
        self.assertEqual(len({part[0]["speaker_id"] for _, _, part in selected}), 4)
        for _, _, parts in selected:
            self.assertFalse({longform.normalize(r["reference"]) for r in parts} & excluded)
        with self.assertRaisesRegex(ValueError, "부족"):
            longform.select_read(rows, {longform.normalize(r["reference"]) for r in rows})

    def test_crop_uses_exact_pcm_frames_and_rejects_out_of_range_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcm = bytes(range(256)) * 4
            source, output = root / "full.wav", root / "clip.wav"
            write_wav(source, pcm)
            before = source.read_bytes()
            bounds = longform.crop(source, output, 11 / 16000, 411 / 16000)
            self.assertEqual(bounds, [11, 411])
            self.assertEqual(longform.read_pcm(output), pcm[22:822])
            self.assertEqual(source.read_bytes(), before)
            missing = root / "invalid.wav"
            with self.assertRaises(ValueError):
                longform.crop(source, missing, 0, 100)
            self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
