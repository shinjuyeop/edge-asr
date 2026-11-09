"""준비 도구의 파일 검증·덮어쓰기 거부. 실제 외부 오디오 다운로드 없음."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edge_asr.tools.data import official_sample as helper


class OfficialSampleTests(unittest.TestCase):
    def test_prepare_binds_transcript_to_hash_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wav = root / helper.WAV
            wav.parent.mkdir(parents=True)
            wav.write_bytes(b"synthetic test fixture")
            checksum = hashlib.sha256(wav.read_bytes()).hexdigest()
            output = root / "data/manifest.jsonl"
            with patch.object(helper, "WAV_SHA256", checksum):
                helper.prepare(root, output)
                before = output.read_bytes()
                helper.prepare(root, output)
            row = json.loads(before)
            self.assertEqual(row["reference"], helper.REFERENCE)
            self.assertEqual(row["audio_sha256"], checksum)
            self.assertIn(helper.REVISION, row["reference_source"])
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(wav.read_bytes(), b"synthetic test fixture")

    def test_wrong_audio_is_rejected_without_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wav = root / helper.WAV
            wav.parent.mkdir(parents=True)
            wav.write_bytes(b"wrong sample")
            output = root / "data/manifest.jsonl"
            with self.assertRaises(ValueError):
                helper.prepare(root, output)
            self.assertFalse(output.exists())

    def test_existing_different_manifest_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wav = root / helper.WAV
            wav.parent.mkdir(parents=True)
            wav.write_bytes(b"synthetic test fixture")
            checksum = hashlib.sha256(wav.read_bytes()).hexdigest()
            output = root / "manifest.jsonl"
            output.write_text("existing labels")
            with patch.object(helper, "WAV_SHA256", checksum), self.assertRaises(ValueError):
                helper.prepare(root, output)
            self.assertEqual(output.read_text(), "existing labels")
