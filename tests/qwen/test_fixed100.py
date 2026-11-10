"""고정 목록의 결정성·중복 제외·원본 형식·경로 보호. 네트워크·ASR 없음."""

import random
import shutil
import tarfile
import tempfile
import unittest
import wave
from pathlib import Path

from edge_asr.dataset import write_wav
from edge_asr.metrics import normalize
from edge_asr.tools.data import fixed100 as prepare


class Fixed100Tests(unittest.TestCase):
    def candidates(self):
        rows = []
        for speaker in range(10):
            for index in range(10):
                frames = (7 if index < 7 else 12) * 16000 + index
                rows.append(
                    {
                        "id": f"z-{speaker}-{index}",
                        "source": "zeroth",
                        "reference": f"제로스 화자{speaker}의 문장{index}",
                        "speaker_id": str(speaker),
                        "gender": "male" if speaker < 4 else "female",
                        "frames": frames,
                    }
                )
        for gender in ("male", "female"):
            for band, seconds in enumerate((8, 12, 17)):
                for index in range(30):
                    rows.append(
                        {
                            "id": f"f-{gender}-{band}-{index}",
                            "source": "fleurs",
                            "reference": f"플뢰르 {gender} 구간{band} 문장{index}",
                            "speaker_id": None,
                            "gender": gender,
                            "frames": seconds * 16000 + index,
                        }
                    )
        return rows

    def test_same_unique100_and_speaker_quota_after_source_order_changes(self):
        source = self.candidates()
        first = prepare.select(source, set())
        random.Random(17).shuffle(source)
        self.assertEqual(first, prepare.select(source, set()))
        self.assertEqual(len({normalize(row["reference"]) for row in first}), 100)
        for speaker in range(10):
            self.assertEqual(
                sum(
                    row["source"] == "zeroth" and row["speaker_id"] == str(speaker) for row in first
                ),
                6,
            )

    def test_normalized_old_reference_exclusion_and_insufficient_pool_do_not_relax_quota(self):
        source = self.candidates()
        first = prepare.select(source, set())
        excluded = normalize(first[0]["reference"] + "!")
        result = prepare.select(source, {excluded})
        self.assertNotIn(excluded, {normalize(row["reference"]) for row in result})
        with self.assertRaises(ValueError):
            prepare.select(
                [r for r in source if r["source"] != "fleurs" or r["gender"] != "female"], set()
            )

    def test_source_hash_mismatch_is_rejected_without_output_or_download(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "zeroth-prefix.bin").write_bytes(b"changed source")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                prepare.check_sources(cache)
            self.assertEqual(list(cache.iterdir()), [cache / "zeroth-prefix.bin"])

    def test_archive_traversal_symlinks_and_oversized_members_rejected(self):
        for name in ("../escape", "/absolute", "test_data_01/../../escape"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare.checked_member(tarfile.TarInfo(name))
        member = tarfile.TarInfo("test_data_01/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../escape"
        with self.assertRaises(ValueError):
            prepare.checked_member(member)
        member = tarfile.TarInfo("test_data_01/large")
        member.size = 17 * 1024**2
        with self.assertRaises(ValueError):
            prepare.checked_member(member)

    def test_flac_format_rejects_truncation_and_wrong_rate(self):
        def blob(rate):
            packed = (rate << 44) | (15 << 36) | 123456
            return b"fLaC\x80\x00\x00\x22" + b"\0" * 10 + packed.to_bytes(8, "big") + b"\0" * 16

        self.assertEqual(prepare.flac_info(blob(16000)), 123456)
        for value in (b"fLaC", blob(48000), b"bad header"):
            with self.assertRaises(ValueError):
                prepare.flac_info(value)

    @unittest.skipUnless(shutil.which("ffmpeg"), "원음 변환 검사는 기존 ffmpeg가 필요합니다.")
    def test_real_conversion_preserves_pcm_and_rejects_wrong_frame_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = path / "original.wav"
            payload = bytes(range(256)) * 8
            write_wav(original, payload)
            output = path / "converted.wav"
            audio = prepare.convert(original, output, len(payload) // 2)
            self.assertEqual(len(audio.samples), len(payload) // 2)
            with wave.open(str(output)) as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), payload)
            with self.assertRaises(ValueError):
                prepare.convert(original, path / "wrong.wav", len(payload) // 2 + 1)
            self.assertEqual(original.read_bytes()[44:], payload)


if __name__ == "__main__":
    unittest.main()
