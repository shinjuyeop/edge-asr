"""검수 WAV의 프레임 경계·변환·원본 보존을 모델 없이 확인한다."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
import zipfile
from array import array
from pathlib import Path

import test_public_vad_review as review_tests

from edge_asr.dataset import load_manifest

ROOT = Path(__file__).resolve().parents[1]
from edge_asr.tools.data import review_audio as helper


def wav(path, frames, rate=16000):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(array("h", (i % 3000 for i in range(frames))).tobytes())


class PublicAudioTests(unittest.TestCase):
    def test_slice_exact_frames_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "source.wav"
            wav(source, 16000)
            before = helper.digest(source)
            info = helper.slice_wav(source, root / "clip.wav", 0.123, 0.8)
            self.assertEqual(info["source_clip_frames_16k"], [1968, 12800])
            with wave.open(str(source)) as a, wave.open(str(root / "clip.wav")) as b:
                a.setpos(1968)
                self.assertEqual(a.readframes(10832), b.readframes(10832))
            for lo, hi in ((-0.1, 0.2), (0.8, 0.2), (0.8, 1.1)):
                with self.assertRaises(ValueError):
                    helper.slice_wav(source, root / "bad.wav", lo, hi)
            with self.assertRaises(FileExistsError):
                helper.slice_wav(source, root / "clip.wav", 0, 0.1)
            self.assertEqual(helper.digest(source), before)

    @unittest.skipUnless(shutil.which("ffmpeg"), "Host ffmpeg 필요")
    def test_real_resampling_packet_integrity_and_no_gold_promotion(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seoul, noise = review_tests.PublicReviewTests().sources(root)
            review = root / "data/review"
            helper_module = importlib.import_module("edge_asr.tools.data.public_review")
            helper_module.prepare(root, seoul, noise, review)
            source = root / "44k.wav"
            wav(source, 441000, 44100)
            flac = root / "source.flac"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(source), str(flac)],
                check=True,
                capture_output=True,
            )
            archive = seoul / "sound.zip"
            with zipfile.ZipFile(archive, "w") as z:
                for name in ("s11m21f1.flac", "s12m21f1.flac"):
                    z.write(flac, name)
            before = {p: helper.digest(p) for p in root.rglob("*") if p.is_file()}
            output = root / "data/audio-review"
            result = helper.materialize(
                root, review, archive, output, use_purpose="noncommercial-research"
            )
            self.assertEqual(result["status"], "human_review_required")
            self.assertFalse(result["ground_truth_finalized"])
            self.assertEqual(result["clips"], 7)
            for name, sha in result["files"].items():
                self.assertEqual(helper.digest(output / name), sha)
            rows = [json.loads(l) for l in (output / "review.jsonl").read_text().splitlines()]
            for row in rows:
                self.assertEqual(helper.digest(root / row["audio_path"]), row["audio_sha256"])
                self.assertIsNone(row["reference"])
                self.assertIsNone(row["expected_speech"])
            self.assertEqual(rows[0]["source_clip_frames_16k"], [0, 11200])
            with self.assertRaises(ValueError):
                load_manifest(output / "review.jsonl", root)
            with self.assertRaises(FileExistsError):
                helper.materialize(
                    root, review, archive, output, use_purpose="noncommercial-research"
                )
            self.assertEqual(before, {p: helper.digest(p) for p in before})
            # 선정 목록 변경을 허용하지 않고 새 결과도 발행하지 않는다.
            with (review / "review-candidates.jsonl").open("a") as f:
                f.write("\n")
            with self.assertRaisesRegex(ValueError, "변경"):
                helper.materialize(
                    root, review, archive, root / "data/bad", use_purpose="noncommercial-research"
                )
            self.assertFalse((root / "data/bad").exists())

    def test_use_purpose_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "이용 목적"):
            helper.materialize(Path("."), Path("."), Path("."), Path("."), use_purpose="unknown")


if __name__ == "__main__":
    unittest.main()
