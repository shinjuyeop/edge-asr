"""Qwen VAD 비교의 사전 계획·구간 동일성·실패 중단 계약. 실제 모델 사용 없음."""

import json
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.dataset import write_wav
from edge_asr.tools.probes import qwen3_realtime as comparison

ROOT = Path(__file__).resolve().parents[1]


class QwenRealtimeTests(unittest.TestCase):
    def test_plan_offline_precedes_online_and_only_qwen2_kss_repeats(self):
        cases = comparison.plan_cases()
        self.assertEqual(len(cases), 13)
        self.assertEqual([c["phase"] for c in cases[:6]], ["offline"] * 6)
        self.assertEqual(
            {(c["family"], c["num_threads"]) for c in cases[:3]}, set(comparison.VARIANTS)
        )
        self.assertEqual(
            [c["id"] for c in cases if c["repeat"] == 2], ["concurrent-kss-qwen3_asr-t2-02"]
        )

    def test_offline_gate_rejects_different_payload_and_failed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [{"id": k, "phase": "offline", "dataset": "kss"} for k in ("one", "two")]
            row = {
                "id": "source",
                "reference": "가",
                "audio": {"sha256": "audio"},
                "status": "success",
                "segmented": {
                    "segments": [
                        {
                            "owned_start_frame": 0,
                            "owned_end_frame": 100,
                            "context_start_frame": 0,
                            "context_end_frame": 100,
                            "payload_sha256": "same",
                            "payload_frames": 100,
                            "reason": "eof",
                        }
                    ]
                },
            }
            for c in cases:
                (root / c["id"]).mkdir()
                write_jsonl(root / c["id"] / "predictions.jsonl", [row])
            self.assertTrue(
                comparison.verify_offline_geometry(root, cases)["same_segments_references_and_pcm"]
            )
            row["segmented"]["segments"][0]["payload_sha256"] = "different"
            write_jsonl(root / "two/predictions.jsonl", [row])
            with self.assertRaises(ValueError):
                comparison.verify_offline_geometry(root, cases)
            row["status"] = "failed"
            write_jsonl(root / "two/predictions.jsonl", [row])
            with self.assertRaises(ValueError):
                comparison.verify_offline_geometry(root, cases)

    def test_queue_loss_exception_never_masks_voltage_native_or_missing_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = {"status": "failed_or_unverified", "exit_code": 1, "limit_verified": True}
            worker = {
                "status": "measurement_with_losses",
                "refine_alive": False,
                "refine_exit_code": 0,
            }
            summary = {
                "input_complete": True,
                "refine": {
                    "dropped_windows": 1,
                    "failed_windows": 0,
                    "missing_results": 0,
                    "successful": 2,
                    "enqueued": 2,
                },
            }
            for name, data in (("execution", receipt), ("worker", worker), ("summary", summary)):
                write_json(root / f"{name}.json", data)
            self.assertTrue(comparison.queue_loss_only(root))
            receipt["rejection"] = "under_voltage"
            write_json(root / "execution.json", receipt)
            self.assertFalse(comparison.queue_loss_only(root))
            receipt.pop("rejection")
            write_json(root / "execution.json", receipt)
            summary["refine"]["missing_results"] = 1
            write_json(root / "summary.json", summary)
            self.assertFalse(comparison.queue_loss_only(root))
            worker["status"] = "measurement_failed"
            write_json(root / "worker.json", worker)
            self.assertFalse(comparison.queue_loss_only(root))

    def test_campaign_stops_after_failure_without_overwriting_or_launching_online(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configs").mkdir()
            for path in comparison.CONFIGS.values():
                (root / path).write_bytes((ROOT / path).read_bytes())
            audio = root / "one.wav"
            write_wav(audio, array("h", [100] * 16000))
            write_jsonl(
                root / "input.jsonl",
                [
                    {
                        "id": "a",
                        "reference": "가",
                        "audio_path": str(audio),
                        "audio_sha256": digest(audio),
                    }
                ],
            )

            def git(command, **kwargs):
                return b"" if "status" in command else "fixture"

            with (
                patch.object(comparison.subprocess, "check_output", side_effect=git),
                patch.dict(
                    comparison.DATASETS, {"kss": "input.jsonl", "synthetic120": "input.jsonl"}
                ),
                patch.object(comparison, "run", side_effect=[0, 1]) as run,
            ):
                self.assertEqual(comparison.campaign(root, root / "out"), 1)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(
                    json.loads((root / "out/campaign.json").read_text())["status"],
                    "stopped_after_failure",
                )
                plan = (root / "out/plan.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    comparison.campaign(root, root / "out")
                self.assertEqual((root / "out/plan.json").read_bytes(), plan)


if __name__ == "__main__":
    unittest.main()
