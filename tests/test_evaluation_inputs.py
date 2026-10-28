"""공통 입력·CER·재채점의 모델 독립 계약."""

import json
from pathlib import Path
import struct
import tempfile
import unittest
import unicodedata
import wave

from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.metrics import character_errors, normalize
from edge_asr.scoring import read_predictions, score_records

ROOT = Path(__file__).resolve().parents[1]


def prediction(identifier, reference, hypothesis, *, elapsed=1.0, duration=2.0, status="success"):
    return {"schema_version": 1, "mode": "batch", "id": identifier, "reference": reference,
            "hypothesis": hypothesis, "audio_path": f"/{identifier}.wav", "split": "evaluation",
            "condition": "clean", "status": status, "elapsed_s": elapsed,
            "process_cpu_s": 0.1, "audio": {"duration_s": duration}, "resources": None}


class CerTests(unittest.TestCase):
    def test_nfc_whitespace_and_punctuation(self):
        decomposed = unicodedata.normalize("NFD", "한글")
        self.assertEqual(character_errors("한 글!\n", decomposed)["cer"], 0)
        self.assertEqual(normalize("1 A!?", "nfc_v1"), "1 A!?")

    def test_substitution_deletion_insertion(self):
        for reference, hypothesis, expected in (("가나", "가다", (1, 0, 0)), ("가나", "가", (0, 1, 0)), ("가", "가나", (0, 0, 1))):
            with self.subTest(reference=reference, hypothesis=hypothesis):
                score = character_errors(reference, hypothesis)
                self.assertEqual(tuple(score[k] for k in ("substitutions", "deletions", "insertions")), expected)

    def test_case_and_numbers_are_preserved(self):
        score = character_errors("12A", "12a")
        self.assertEqual((score["errors"], score["reference_characters"]), (1, 3))

    def test_empty_reference_and_cer_above_one(self):
        self.assertIsNone(character_errors(" !\t", "가")["cer"])
        self.assertEqual(character_errors("가", "가나다")["cer"], 2)

    def test_deterministic_tie_break(self):
        score = character_errors("ab", "ba")
        self.assertEqual((score["substitutions"], score["deletions"], score["insertions"]), (2, 0, 0))

    def test_corpus_weights_and_failure_coverage(self):
        rows = [prediction("short", "가가", "나가", duration=2),
                prediction("long", "가" * 8, "나" + "가" * 7, duration=8),
                prediction("empty", "", "나"),
                prediction("failed", "가" * 10, None, status="failed")]
        summary, scores = score_records(rows, "ko_cer_v1")
        overall = summary["overall"]
        self.assertAlmostEqual(overall["cer"]["value"], 0.2)
        self.assertAlmostEqual(overall["rtf"], 3 / 12)
        self.assertEqual(overall["cer"]["empty_reference_nonempty_hypotheses"], 1)
        self.assertEqual(overall["reference_character_coverage"], 0.5)
        self.assertFalse(overall["complete"])
        self.assertIsNone(scores[2]["cer"])

    def test_all_empty_or_failed_is_not_zero_cer(self):
        for rows in ([prediction("empty", "", "")], [prediction("failed", "정답", None, status="failed")]):
            summary, _ = score_records(rows, "ko_cer_v1")
            self.assertIsNone(summary["overall"]["cer"]["value"])


class InputTests(unittest.TestCase):
    def test_manifest_root_and_empty_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "list.jsonl"
            manifest.write_text(json.dumps({"id": "a", "audio_path": "data/a.wav", "reference": ""}) + "\n")
            rows, checksum = load_manifest(manifest, root)
            self.assertEqual(rows[0].audio_path, root / "data/a.wav")
            self.assertEqual(rows[0].reference, "")
            self.assertEqual(len(checksum), 64)

    def test_manifest_invalid_rows(self):
        examples = ["", "[]\n", '{"id":"a"}\n',
                    '\n'.join([json.dumps({"id": "same", "audio_path": "a.wav", "reference": "가"})] * 2)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for text in examples:
                with self.subTest(text=text):
                    path = root / "input.jsonl"
                    path.write_text(text)
                    with self.assertRaises(ValueError):
                        load_manifest(path, root)

    def test_invalid_expected_hash_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "manifest.jsonl"
            row = {"id": "a", "audio_path": "a.wav", "reference": "가", "audio_sha256": "not-a-sha256"}
            path.write_text(json.dumps(row))
            with self.assertRaises(ValueError):
                load_manifest(path, root)

    def test_audio_values_format_and_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(struct.pack("<hhh", -32768, 0, 16384))
            audio = load_audio(path, 16000)
            self.assertEqual(list(audio.samples), [-1.0, 0.0, 0.5])
            self.assertEqual(audio.duration_s, 3 / 16000)
            with self.assertRaises(ValueError):
                load_audio(path, 8000)
            path.write_bytes(path.read_bytes()[:-2])
            with self.assertRaises(ValueError):
                load_audio(path, 16000)

    def test_eval_config_and_invalid_options(self):
        source = (ROOT / "configs/zipformer_eval.toml").read_text()
        self.assertEqual(load_evaluation_config(ROOT / "configs/zipformer_eval.toml")["model"]["num_threads"], 2)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            for before, after in (("num_threads = 2", "num_threads = true"),
                                  ("warmup_runs = 3", "warmup_runs = -1"),
                                  ('mode = "batch"', 'mode = "paced_stream"'),
                                  ('family = "zipformer"', 'family = ["zipformer"]'),
                                  ("warmup_runs = 3", "warmup_run = 3"),
                                  ("tail_padding_s = 0.66", "tail_padding_s = nan")):
                with self.subTest(after=after):
                    path.write_text(source.replace(before, after))
                    with self.assertRaises(ValueError):
                        load_evaluation_config(path)

    def test_prediction_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "predictions.jsonl"
            for row in (prediction("a", "가", "가", elapsed=float("nan")),
                        prediction("a", "가", "가", duration=0)):
                path.write_text(json.dumps(row) + "\n")
                with self.assertRaises(ValueError):
                    read_predictions(path)
            valid = json.dumps(prediction("a", "가", "가")) + "\n"
            path.write_text(valid + valid)
            with self.assertRaises(ValueError):
                read_predictions(path)
