"""Qwen 비교 계획의 고정 순서·실패 후 중단·재실행 보존을 모델 없이 검사한다."""

import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from edge_asr.legacy.tools.probes import qwen3_comparison as comparison

ROOT = Path(__file__).resolve().parents[2]


class QwenComparisonTests(unittest.TestCase):
    def test_plan_is_complete_unique_and_fixed_before_results(self):
        cases = comparison.plan_cases()
        self.assertEqual(len({(c["family"], c["num_threads"]) for c in cases}), 9)
        self.assertEqual([c["family"] for c in cases[:3]], ["qwen3_asr", "zipformer", "sensevoice"])
        self.assertEqual(cases, comparison.plan_cases())

    def test_campaign_stops_after_failure_and_preserves_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "configs/legacy").mkdir(parents=True)
            for filename in comparison.CONFIGS.values():
                (root / filename).write_bytes((ROOT / filename).read_bytes())
            audio = root / "one.wav"
            with wave.open(str(audio), "wb") as w:
                w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                w.writeframes(b"\0\0" * 16000)
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "id": "one",
                        "audio_path": "one.wav",
                        "reference": "안녕",
                        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                    }
                )
                + "\n"
            )

            def git(command, **kwargs):
                return b"" if "status" in command else "fixture-commit"

            with (
                patch.object(comparison.subprocess, "check_output", side_effect=git),
                patch.object(comparison, "run", side_effect=[0, 1]) as run,
            ):
                output = root / "new-campaign"
                self.assertEqual(comparison.campaign(root, output, manifest), 1)
                plan = (output / "plan.json").read_bytes()
                self.assertEqual(run.call_count, 2)
                self.assertEqual(json.loads(plan)["repetitions_per_configuration"], 1)
                result = json.loads((output / "campaign.json").read_text())
                self.assertEqual(result["status"], "stopped_after_failure")
                self.assertEqual(len(result["cases"]), 2)
                with self.assertRaises(FileExistsError):
                    comparison.campaign(root, output, manifest)
                self.assertEqual((output / "plan.json").read_bytes(), plan)
