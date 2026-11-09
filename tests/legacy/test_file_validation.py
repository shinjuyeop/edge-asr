"""새 문장 분리·출처 고정·연속 PCM 보존을 가짜 자료로 검사한다."""

import json
import shutil
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from edge_asr.artifacts import digest, read_jsonl, write_jsonl
from edge_asr.dataset import load_audio, write_wav
from edge_asr.legacy.tools.probes import file_validation as campaign
from edge_asr.tools.data import external, validation

ROOT = Path(__file__).resolve().parents[2]


class FileValidationCampaignTests(unittest.TestCase):
    def test_fixed_plan_has_two_offline_gates_and_two_continuous_half_hours(self):
        cases = campaign.plan_cases()
        self.assertEqual(len(cases), 6)
        self.assertEqual([c["phase"] for c in cases], ["offline"] * 2 + ["concurrent"] * 4)
        self.assertEqual([c["timeout_s"] for c in cases], [900] * 4 + [2100] * 2)
        self.assertEqual({c["num_threads"] for c in cases}, {2})
        self.assertEqual([c["family"] for c in cases[-2:]], ["sensevoice", "qwen3_asr"])

    def test_failure_stops_plan_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "configs/legacy").mkdir(parents=True)
            for path in campaign.CONFIGS.values():
                (root / path).write_bytes((ROOT / path).read_bytes())
            with (
                patch.object(
                    campaign.subprocess, "check_output", side_effect=[b"", "fixture", b""]
                ),
                patch.object(campaign, "validate_inputs", return_value=({}, {})),
                patch.object(campaign, "run", side_effect=[0, 1]) as run,
                patch.object(campaign, "verify_offline_geometry") as geometry,
            ):
                self.assertEqual(campaign.campaign(root, root / "out"), 1)
                self.assertEqual(run.call_count, 2)
                geometry.assert_not_called()
                status = json.loads((root / "out/campaign.json").read_text())
                self.assertEqual(status["status"], "stopped_after_failure")
                self.assertIn("finished_at_utc", status)
                before = (root / "out/plan.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    campaign.campaign(root, root / "out")
                self.assertEqual((root / "out/plan.json").read_bytes(), before)

    def test_successful_sequence_passes_separate_warmup_and_only_sustained_gets_2100s(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "configs/legacy").mkdir(parents=True)
            for path in campaign.CONFIGS.values():
                (root / path).write_bytes((ROOT / path).read_bytes())
            with (
                patch.object(campaign.subprocess, "check_output", side_effect=[b"", "fixture"]),
                patch.object(campaign, "validate_inputs", return_value=({}, {})),
                patch.object(campaign, "run", return_value=0) as run,
                patch.object(campaign, "verify_offline_geometry", return_value={}) as geometry,
            ):
                self.assertEqual(campaign.campaign(root, root / "out"), 0)
                geometry.assert_called_once()
                for call, case in zip(run.call_args_list, campaign.plan_cases()):
                    self.assertEqual(call.kwargs["timeout_s"], case["timeout_s"])
                    self.assertEqual(call.kwargs["temperature_policy"], "operational")
                    self.assertIn("--warmup-wav", call.args[2])
                    self.assertNotIn("--warmup-first-window", call.args[2])
                self.assertEqual(
                    json.loads((root / "out/campaign.json").read_text())["status"], "completed"
                )


class FileValidationDataTests(unittest.TestCase):
    def rows(self):
        return [
            {"row_idx": i, "row": {"id": i, "transcription": text}}
            for i, text in enumerate(("기존", "같은 문장!", "새 문장", "새문장.", "다음 문장"))
        ]

    def test_selection_excludes_sentence_ids_normalized_text_and_internal_duplicates(self):
        seen = [
            {"sentence_id": 0, "reference": "다른 전사"},
            {"sentence_id": 99, "reference": "같은문장"},
        ]
        selected, decisions = validation.select_rows(self.rows(), seen, count=2)
        self.assertEqual([r["row_idx"] for r in selected], [2, 4])
        self.assertEqual(
            [r["decision"] for r in decisions],
            [
                "sentence_id_seen",
                "normalized_reference_seen",
                "selected",
                "normalized_reference_seen",
                "selected",
            ],
        )
        with self.assertRaises(ValueError):
            validation.select_rows(self.rows(), seen, count=3)

    def test_validation_revision_split_url_and_offset_are_bound(self):
        row = {
            "row_idx": 5,
            "truncated_cells": [],
            "row": {
                "language": "Korean",
                "transcription": "가",
                "num_samples": 16000,
                "audio": [
                    {
                        "src": f"https://datasets-server.huggingface.co/cached-assets/google/fleurs/--/{external.REVISION}/--/ko_kr/validation/5/audio/audio.wav"
                    }
                ],
            },
        }
        value = {"partial": False, "rows": [row]}
        self.assertEqual(
            external.validate_rows(
                value, external.REVISION, source_split="validation", offset=5, count=1
            ),
            [row],
        )
        for revision, split, offset in (
            ("wrong", "validation", 5),
            (external.REVISION, "test", 5),
            (external.REVISION, "validation", 4),
        ):
            with self.assertRaises(ValueError):
                external.validate_rows(value, revision, source_split=split, offset=offset, count=1)

    def test_sustained_wav_repeats_every_frame_and_reference_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "data/evaluation/m23-stress"
            base.mkdir(parents=True)
            source = base / "source.wav"
            pcm = array("h", [100, -99, 5] * 537)
            write_wav(source, pcm)
            write_jsonl(
                base / "long-120.jsonl",
                [
                    {
                        "id": "source",
                        "reference": "원문 1",
                        "audio_path": str(source),
                        "audio_sha256": digest(source),
                    }
                ],
            )
            before = digest(source)
            result = validation.prepare_sustained(root, root / "data/new", repeats=3)
            with wave.open(str(root / "data/new/continuous.wav"), "rb") as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), pcm.tobytes() * 3)
            self.assertEqual(
                read_jsonl(root / "data/new/manifest.jsonl")[0]["reference"], "원문 1 원문 1 원문 1"
            )
            self.assertEqual(result["frames"], len(pcm) * 3)
            self.assertEqual(digest(source), before)
            self.assertEqual(
                validation.prepare_sustained(root, root / "data/new", repeats=3), result
            )
            (root / "data/new/continuous.wav").write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                validation.prepare_sustained(root, root / "data/new", repeats=3)

    @unittest.skipUnless(shutil.which("ffmpeg"), "Host ffmpeg 필요")
    def test_new_sentences_keep_provenance_and_fail_without_replacing_duplicate_audio(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                wavs = []
                for index in range(2):
                    wav = root / f"{index}.wav"
                    write_wav(wav, array("h", [index + 100] * 1600))
                    wavs.append(wav.read_bytes())
                for name in ("fleurs-ko-test100", "kss-dev50"):
                    folder = root / "data/evaluation" / name
                    folder.mkdir(parents=True)
                    write_jsonl(
                        folder / "manifest.jsonl",
                        [{"reference": "기존", "sentence_id": 200, "audio_sha256": "old"}],
                    )
                rows = []
                for i in range(100):
                    rows.append(
                        {
                            "row_idx": i,
                            "truncated_cells": [],
                            "row": {
                                "id": i,
                                "gender": i % 2,
                                "language": "Korean",
                                "num_samples": 1600,
                                "transcription": f"새 문장 {i}",
                                "audio": [
                                    {
                                        "src": f"https://datasets-server.huggingface.co/cached-assets/google/fleurs/--/{external.REVISION}/--/ko_kr/validation/{i}/audio/audio.wav"
                                    }
                                ],
                            },
                        }
                    )

                def fetch(url, limit):
                    if "/rows?" in url:
                        return json.dumps({"rows": rows, "partial": False}).encode(), {
                            "x-revision": external.REVISION
                        }
                    if url.endswith("README.md"):
                        return b"CC BY 4.0", {}
                    i = int(url.split("/validation/")[1].split("/")[0])
                    return wavs[0 if duplicate else i], {}

                output = root / "data/new"
                with (
                    patch.object(validation, "fetch", side_effect=fetch),
                    patch.object(validation, "COUNT", 2),
                ):
                    if duplicate:
                        with self.assertRaises(ValueError):
                            validation.prepare_fleurs(root, output)
                        self.assertEqual(
                            json.loads((output / "preparation.json").read_text())["status"],
                            "failed",
                        )
                        self.assertFalse((output / "manifest.jsonl").exists())
                    else:
                        result = validation.prepare_fleurs(root, output)
                        self.assertEqual(len(read_jsonl(output / "manifest.jsonl")), 2)
                        self.assertEqual(validation.prepare_fleurs(root, output), result)
                        self.assertFalse(result["speaker_independence_verified"])
                        self.assertEqual(load_audio(output / "wav/0000.wav", 16000).duration_s, 0.1)
