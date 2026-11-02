"""모델 없는 합성 WAV로 혼합·개발/보류 경계·해시/원본 보존을 검사한다."""

import json
import tempfile
import unittest
from array import array
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.dataset import load_audio, write_wav
from edge_asr.tools.data.vad_overlap import mix_pcm, prepare


class OverlapTests(unittest.TestCase):
    def test_circular_average_bounds_phase_and_single_track(self):
        self.assertEqual(
            mix_pcm([100, -100, 300, -300], [0], 6), array("h", [100, -100, 300, -300, 100, -100])
        )
        self.assertEqual(
            mix_pcm([100, -100, 300, -300], [0, 2], 4), array("h", [200, -200, 200, -200])
        )
        self.assertEqual(mix_pcm([-32768, 32767], [0, 1], 2), array("h", [0, 0]))
        for args in (([], [0], 4), ([0], [], 4), ([0], [1], 4), ([32768], [0], 4), ([0], [0], 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mix_pcm(*args)

    def fixture(self, root, split="development"):
        data = root / "data"
        data.mkdir()
        wav = data / "source.wav"
        write_wav(wav, array("h", [1000, -2000, 3000, -4000] * 100))
        manifest = data / "source.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "id": "synthetic",
                    "audio_path": "data/source.wav",
                    "audio_sha256": digest(wav),
                    "reference": "fixture",
                    "split": split,
                }
            )
            + "\n"
        )
        return wav, manifest

    def test_three_cases_are_reproducible_and_source_and_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav, manifest = self.fixture(root)
            before = digest(wav)
            a, b = root / "data/a", root / "data/b"
            first = prepare(root, manifest, a, duration_s=1)
            second = prepare(root, manifest, b, duration_s=1)
            for n in (1, 2, 3):
                self.assertEqual(
                    digest(a / f"tracks-{n}/audio.wav"), digest(b / f"tracks-{n}/audio.wav")
                )
                spec = json.loads((a / f"tracks-{n}/manifest.json").read_text())
                self.assertEqual(spec["frames"], 16000)
                self.assertIsNone(spec["reference"])
                self.assertEqual(len(load_audio(a / f"tracks-{n}/audio.wav", 16000).samples), 16000)
            self.assertEqual(digest(wav), before)
            self.assertEqual(first["source_manifest_sha256"], second["source_manifest_sha256"])
            with self.assertRaises(FileExistsError):
                prepare(root, manifest, a, duration_s=1)

    def test_holdout_and_tampered_audio_rejected_before_new_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav, manifest = self.fixture(root, "holdout")
            out = root / "data/new"
            with self.assertRaisesRegex(ValueError, "개발"):
                prepare(root, manifest, out, duration_s=1)
            self.assertFalse(out.exists())
            row = json.loads(manifest.read_text())
            row["split"] = "development"
            row["audio_sha256"] = "0" * 64
            manifest.write_text(json.dumps(row) + "\n")
            with self.assertRaisesRegex(ValueError, "해시|SHA"):
                prepare(root, manifest, out, duration_s=1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
