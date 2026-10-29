"""외부 자료 출처 검증과 합성 스트레스 입력의 정답·프레임 보존."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from edge_asr.tools.data import external as helper


def payload():
    return {
        "partial": False,
        "rows": [
            {
                "row_idx": i,
                "truncated_cells": [],
                "row": {
                    "language": "Korean",
                    "transcription": "정답",
                    "num_samples": 16000,
                    "audio": [
                        {
                            "src": f"https://datasets-server.huggingface.co/cached-assets/google/fleurs/--/{helper.REVISION}/--/ko_kr/test/{i}/audio/audio.wav"
                        }
                    ],
                },
            }
            for i in range(helper.COUNT)
        ],
    }


class DataTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "Host ffmpeg가 필요합니다")
    def test_external_preparation_conversion_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(helper, "COUNT", 2):
            root = Path(directory)
            path = root / "source.wav"
            helper.write_wav(path, b"\1\0" * 16000)
            blob = path.read_bytes()
            rows = payload()
            for i, item in enumerate(rows["rows"]):
                item["row"].update(id=i, gender=i)

            def fetch(url, limit):
                if "/rows?" in url:
                    return json.dumps(rows).encode(), {"x-revision": helper.REVISION}
                return (b"license: cc-by-4.0" if url.endswith("README.md") else blob), {}

            destination = root / "data/evaluation/fleurs-fixture"
            with patch.object(helper, "fetch", side_effect=fetch):
                result = helper.prepare_fleurs(root, destination)
            self.assertEqual(result["unique_sentence_ids"], 2)
            self.assertEqual(result["speaker_count"], None)
            self.assertEqual(len(helper.load_manifest(destination / "manifest.jsonl", root)[0]), 2)
            self.assertEqual(helper.prepare_fleurs(root, destination), result)
            (destination / "wav/0000.wav").write_bytes(b"modified")
            with self.assertRaises(ValueError):
                helper.prepare_fleurs(root, destination)

    def test_external_source_revision_rows_reference_and_url_checked(self):
        source = payload()
        self.assertEqual(len(helper.validate_rows(source, helper.REVISION)), 100)
        with self.assertRaises(ValueError):
            helper.validate_rows(source, "changed")
        for mutation in (
            lambda p: p["rows"].pop(),
            lambda p: p["rows"][0]["row"].update(transcription=""),
            lambda p: p["rows"][0]["row"].update(audio=[{"src": "https://example.com/audio.wav"}]),
            lambda p: p["rows"][1].update(row_idx=0),
        ):
            broken = copy.deepcopy(source)
            mutation(broken)
            with self.assertRaises(ValueError):
                helper.validate_rows(broken, helper.REVISION)

    def test_stress_silence_complete_utterances_and_long_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kss = root / "data/evaluation/kss-dev50"
            kss.mkdir(parents=True)
            rows = []
            for index in range(12):
                path = kss / f"{index}.wav"
                helper.write_wav(path, b"\1\0" * (16000 * (index + 10)))
                rows.append(
                    {
                        "id": str(index),
                        "audio_path": str(path.relative_to(root)),
                        "audio_sha256": helper.sha256(path),
                        "reference": f"문장{index}",
                    }
                )
            helper.write_manifest(kss / "manifest.jsonl", rows)
            destination = root / "data/evaluation/stress"
            result = helper.prepare_stress(root, destination)
            self.assertEqual(helper.prepare_stress(root, destination), result)
            silence, _ = helper.load_manifest(destination / "silence.jsonl", root)
            self.assertEqual(len(silence), 6)
            for sample, duration in zip(silence, result["recipe"]["silence_s"]):
                audio = helper.load_audio(sample.audio_path, 16000)
                self.assertEqual(audio.duration_s, duration)
                self.assertEqual(sample.reference, "")
                self.assertFalse(any(audio.samples))
            long, _ = helper.load_manifest(destination / "long-120.jsonl", root)
            details = result["synthetic_long"][-1]
            self.assertEqual(
                long[0].reference,
                " ".join(rows[int(i)]["reference"] for i in details["source_ids"]),
            )
            self.assertEqual(
                helper.load_audio(long[0].audio_path, 16000).duration_s, details["actual_s"]
            )
            self.assertGreaterEqual(details["actual_s"], 120)
            (destination / "silence.jsonl").write_text("changed")
            with self.assertRaises(ValueError):
                helper.prepare_stress(root, destination)


if __name__ == "__main__":
    unittest.main()
