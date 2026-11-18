"""고정 목록의 결정성·중복 제외·원본 형식·경로 보호. 네트워크·ASR 없음."""

import random
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import wave
from io import BytesIO
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.dataset import write_wav
from edge_asr.metrics import normalize
from edge_asr.tools.data import fixed100 as prepare
from edge_asr.tools.data import zeroth
from edge_asr.tools.data.external import checked_member, convert


class Fixed100Tests(unittest.TestCase):
    def test_zeroth_archive_and_candidates_preserve_order_references_and_frames(self):
        files = {
            "AUDIO_INFO": (
                "speaker|age|sex|script|split\n"
                + "\n".join(
                    f"s{i}|30|{'m' if i % 2 else 'f'}|script|test_data_01" for i in range(10)
                )
            ).encode(),
        }
        references, expected = [], []
        for i in range(457):
            speaker, key, frames = f"s{i % 10}", f"utt-{i:03d}", 16000 + i
            name = f"test_data_01/script/{speaker}/{key}.flac"
            packed = (16000 << 44) | (15 << 36) | frames
            files[name] = (
                b"fLaC\x80\x00\x00\x22" + b"\0" * 10 + packed.to_bytes(8, "big") + b"\0" * 16
            )
            references.append(f"{key} 원본 문장 {i}!")
            expected.append(
                {
                    "id": "zeroth-" + key,
                    "source": "zeroth",
                    "member": name,
                    "reference": f"원본 문장 {i}!",
                    "frames": frames,
                    "speaker_id": speaker,
                    "gender": "male" if i % 2 else "female",
                    "source_split": "test_data_01",
                }
            )
        files["test_data_01/script/all.trans.txt"] = "\n".join(references).encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "zeroth-prefix.bin"
            with tarfile.open(archive, "w:gz") as stream:
                for name, blob in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(blob)
                    stream.addfile(member, BytesIO(blob))
                boundary = tarfile.TarInfo("train_data_01")
                boundary.type = tarfile.DIRTYPE
                stream.addfile(boundary)
            original = archive.read_bytes()
            collected = zeroth.members(root)
            self.assertEqual(list(collected), list(files))
            self.assertEqual(collected, files)
            self.assertEqual(zeroth.candidates(collected), expected)
            self.assertEqual(archive.read_bytes(), original)
        files["test_data_01/script/duplicate.trans.txt"] = references[0].encode()
        with self.assertRaisesRegex(ValueError, "중복 Zeroth 전사 ID"):
            zeroth.candidates(files)

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
                checked_member(tarfile.TarInfo(name))
        member = tarfile.TarInfo("test_data_01/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../escape"
        with self.assertRaises(ValueError):
            checked_member(member)
        member = tarfile.TarInfo("test_data_01/large")
        member.size = 17 * 1024**2
        with self.assertRaises(ValueError):
            checked_member(member)

    def test_flac_format_rejects_truncation_and_wrong_rate(self):
        def blob(rate):
            packed = (rate << 44) | (15 << 36) | 123456
            return b"fLaC\x80\x00\x00\x22" + b"\0" * 10 + packed.to_bytes(8, "big") + b"\0" * 16

        self.assertEqual(zeroth.flac_info(blob(16000)), 123456)
        for value in (b"fLaC", blob(48000), b"bad header"):
            with self.assertRaises(ValueError):
                zeroth.flac_info(value)

    @unittest.skipUnless(shutil.which("ffmpeg"), "원음 변환 검사는 기존 ffmpeg가 필요합니다.")
    def test_real_conversion_preserves_pcm_and_rejects_wrong_frame_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = path / "original.wav"
            payload = bytes(range(256)) * 8
            write_wav(original, payload)
            output = path / "converted.wav"
            audio = convert(original, output, len(payload) // 2)
            self.assertEqual(len(audio.samples), len(payload) // 2)
            with wave.open(str(output)) as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), payload)
            with self.assertRaises(ValueError):
                convert(original, path / "wrong.wav", len(payload) // 2 + 1)
            self.assertEqual(original.read_bytes()[44:], payload)
            before = digest(original), digest(output)
            with self.assertRaises(subprocess.CalledProcessError):
                convert(original, output, len(payload) // 2)
            self.assertEqual((digest(original), digest(output)), before)


if __name__ == "__main__":
    unittest.main()
