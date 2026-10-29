"""고정 데이터 출처·기존 파일 보호·실제 ffmpeg 변환 검사. 외부 네트워크는 모의한다."""

import copy
import io
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from edge_asr.dataset import load_audio, load_manifest
from edge_asr.tools.data import kss as helper


def payload():
    rows = []
    for index in range(helper.OFFSET, helper.OFFSET + helper.COUNT):
        url = (
            f"https://datasets-server.huggingface.co/cached-assets/{helper.DATASET}/--/"
            f"{helper.REVISION}/--/default/train/{index}/audio/audio.wav?Expires=fixture"
        )
        rows.append(
            {
                "row_idx": index,
                "row": {"expanded_script": f"합성 정답 {index}.", "audio": [{"src": url}]},
                "truncated_cells": [],
            }
        )
    return {"rows": rows, "partial": False}


def synthetic_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        wav.writeframes(struct.pack("<h", 1000) * 4410)
    return buffer.getvalue()


class SourceValidationTests(unittest.TestCase):
    def test_changed_revision_rejected(self):
        with self.assertRaisesRegex(ValueError, "리비전"):
            helper.validate_rows(payload(), "different-revision")

    def test_incomplete_duplicate_wrong_asset_and_empty_reference_rejected(self):
        original = payload()
        broken = []
        fewer = copy.deepcopy(original)
        fewer["rows"].pop()
        broken.append(fewer)
        duplicate = copy.deepcopy(original)
        duplicate["rows"][1]["row_idx"] = helper.OFFSET
        broken.append(duplicate)
        wrong_asset = copy.deepcopy(original)
        wrong_asset["rows"][0]["row"]["audio"][0]["src"] = "https://example.com/audio.wav"
        broken.append(wrong_asset)
        empty = copy.deepcopy(original)
        empty["rows"][0]["row"]["expanded_script"] = " "
        broken.append(empty)
        for value in broken:
            with self.subTest(value=value["rows"][0]["row_idx"]), self.assertRaises(ValueError):
                helper.validate_rows(value, helper.REVISION)

    def test_existing_unrelated_directory_and_outside_data_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "data/existing"
            target.mkdir(parents=True)
            old = target / "existing.txt"
            old.write_text("keep")
            with patch.object(helper, "fetch") as fetch:
                with self.assertRaises(OSError):
                    helper.prepare(root, target)
                with self.assertRaises(ValueError):
                    helper.prepare(root, root / "models/subset")
                fetch.assert_not_called()
            self.assertEqual(old.read_text(), "keep")


@unittest.skipUnless(shutil.which("ffmpeg"), "Host ffmpeg 미설치")
class PreparationTests(unittest.TestCase):
    def fetch(self, url, limit):
        if "/rows?" in url:
            return json.dumps(payload(), ensure_ascii=False).encode(), {
                "x-revision": helper.REVISION
            }
        if url.endswith("README.md"):
            return b"Synthetic dataset card", {}
        return synthetic_wav(), {}

    def test_conversion_manifest_offline_reuse_and_corruption_guard(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(helper, "COUNT", 2):
            root = Path(temp)
            target = root / "data/evaluation/subset"
            with patch.object(helper, "fetch", side_effect=self.fetch):
                manifest = helper.prepare(root, target)
            samples, _ = load_manifest(manifest, root)
            self.assertEqual(len(samples), 2)
            self.assertEqual(samples[0].reference, "합성 정답 1000.")
            audio = load_audio(samples[0].audio_path, 16000)
            self.assertAlmostEqual(audio.duration_s, 0.1)
            self.assertEqual(samples[0].audio_sha256, audio.sha256)
            self.assertEqual((target / "original/01000.wav").read_bytes(), synthetic_wav())
            manifest_before = manifest.read_bytes()
            with patch.object(helper, "fetch") as fetch:
                self.assertEqual(helper.prepare(root, target, ffmpeg="not-installed"), manifest)
                fetch.assert_not_called()
            samples[0].audio_path.write_bytes(b"altered audio")
            with self.assertRaisesRegex(ValueError, "변경"):
                helper.prepare(root, target)
            self.assertEqual(samples[0].audio_path.read_bytes(), b"altered audio")
            self.assertEqual(manifest.read_bytes(), manifest_before)

    def test_failed_download_publishes_no_partial_dataset_and_preserves_sibling(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(helper, "COUNT", 2):
            root = Path(temp)
            target = root / "data/evaluation/subset"
            target.parent.mkdir(parents=True)
            sibling = target.parent / "original.wav"
            sibling.write_bytes(b"existing file")

            def fail(url, limit):
                if "/1001/audio/" in url:
                    raise OSError("connection lost")
                return self.fetch(url, limit)

            with patch.object(helper, "fetch", side_effect=fail), self.assertRaises(OSError):
                helper.prepare(root, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.iterdir()), [sibling])
            self.assertEqual(sibling.read_bytes(), b"existing file")
