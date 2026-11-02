"""실제 모델 없이 PCM/구간/빈 검출/실패/CER 재채점·모델 재사용 계약을 검사한다."""

import copy
import json
import tempfile
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from edge_asr.artifacts import digest, write_jsonl
from edge_asr.dataset import Audio, write_wav
from edge_asr.metrics import character_errors
from edge_asr.scoring import read_predictions, score_records
from edge_asr.tools.probes import segmented_accuracy as probe
from edge_asr.tools.reports.segmented_accuracy import summarize, verify
from edge_asr.vad_probability import BoundedProbabilityVad

ROOT = Path(__file__).resolve().parents[1]


class Probability:
    def __init__(self, value):
        self.value, self.reset_count = value, 0

    def reset(self):
        self.reset_count += 1

    def __call__(self, block):
        return self.value


def adapter():
    model = Mock(model_files={}, metadata={})
    model.last_result_metadata = {"raw_text": "<|ko|>가", "special_tags": ["<|ko|>"]}
    model.transcribe.return_value = "가"
    return model


def prediction(audio, result):
    return {
        "schema_version": 1,
        "mode": "batch",
        "id": "one",
        "audio_path": "unused.wav",
        "reference": "가나",
        "split": "development",
        "condition": "clean",
        "status": result["status"],
        "hypothesis": result["hypothesis"],
        "audio": audio.metadata(),
        "elapsed_s": result["pipeline_wall_s"],
        "process_cpu_s": result["pipeline_cpu_s"],
        "resources": {"rss_observed_max_mib": None},
        "error": result["error"],
        "segmentation_condition": result["condition"],
        "segmented": result,
    }


class SegmentedAccuracyTests(unittest.TestCase):
    def audio(self, frames=6 * 16000 + 137):
        return Audio(array("f", ((i % 200 - 100) / 32768 for i in range(frames))), 16000, "wav")

    def vad(self, value):
        return BoundedProbabilityVad(probe.MeasuredProbability(Probability(value)))

    def test_same_pcm_boundaries_no_context_padding_or_mid_utterance_reset(self):
        audio = self.audio()
        hashes = []
        for condition in probe.CONDITIONS:
            with self.subTest(condition=condition):
                model, vad = adapter(), self.vad(1)
                result = probe.measure(audio, condition, model, vad)
                self.assertEqual(result["status"], "success")
                hashes.append(result["input_pcm_sha256"])
                verify(prediction(audio, result))
                received = array("f")
                for call in model.transcribe.call_args_list:
                    received.extend(call.args[0].samples)
                self.assertEqual(received, audio.samples)
                self.assertEqual(result["selected_frames"], len(audio.samples))
                self.assertEqual(model.transcribe.call_count, 1 if condition == "full_batch" else 2)
                self.assertEqual(
                    result["segments"][0]["adapter_result"]["special_tags"], ["<|ko|>"]
                )
                if condition == "bounded_vad_5s":
                    self.assertEqual(result["vad_resets_during_utterance"], 1)
                    self.assertEqual(result["pipeline"]["context_frames"], 0)
                    self.assertGreater(result["vad_inference_s"], 0)
        self.assertEqual(len(set(hashes)), 1)

    def test_no_dedup_or_text_filter_and_segment_failure_never_falls_back(self):
        audio, model = self.audio(), adapter()
        model.transcribe.side_effect = ["같은 말", "같은 말"]
        result = probe.measure(audio, "fixed_windows_5s", model)
        self.assertEqual(result["hypothesis"], "같은 말 같은 말")
        model.transcribe.side_effect = ["첫 구간", RuntimeError("segment failed")]
        result = probe.measure(audio, "fixed_windows_5s", model)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["hypothesis"])
        self.assertEqual([r["status"] for r in result["segments"]], ["success", "failed"])
        self.assertEqual(model.transcribe.call_count, 4)

    def test_empty_vad_is_successful_empty_hypothesis_with_full_reference_deletions(self):
        audio, model = self.audio(10000), adapter()
        result = probe.measure(audio, "bounded_vad_5s", model, self.vad(0))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["hypothesis"], "")
        self.assertEqual(result["segments"], [])
        model.transcribe.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "predictions.jsonl"
            write_jsonl(raw, [prediction(audio, result)])
            summary = summarize(raw, root)
            self.assertEqual(summary["overall"]["cer"]["deletions"], 2)
            self.assertEqual(summary["overall"]["cer"]["value"], 1)
            self.assertEqual(summary["segmented_accuracy"]["empty_segment_files"], 1)
            self.assertEqual(summary["segmented_accuracy"]["asr_wall_s"], 0)
            self.assertIsNone(summary["unmeasured"]["backlog"])

    def test_scorer_replays_without_models_or_audio_and_rejects_corrupt_geometry(self):
        audio, model = self.audio(), adapter()
        model.transcribe.side_effect = ["가", "나"]
        row = prediction(audio, probe.measure(audio, "fixed_windows_5s", model))
        self.assertEqual(
            character_errors(row["reference"], row["hypothesis"], "ko_cer_v1")["errors"], 0
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "predictions.jsonl"
            write_jsonl(raw, [row])
            first = summarize(raw, root)
            replay = root / "rescore"
            replay.mkdir()
            self.assertEqual(first, summarize(raw, replay))
            common, _ = score_records(read_predictions(raw), "ko_cer_v1")
            self.assertEqual(common["overall"], first["overall"])
            self.assertEqual(first["by_length"]["over_5s"]["successful"], 1)
            for corruption in ("overlap", "join", "hash", "cost"):
                bad = copy.deepcopy(row)
                if corruption == "overlap":
                    bad["segmented"]["segments"][1]["owned_start_frame"] = 0
                elif corruption == "join":
                    bad["hypothesis"] = "edited"
                elif corruption == "hash":
                    bad["segmented"]["input_pcm_sha256"] = "bad"
                else:
                    bad["elapsed_s"] += 1
                with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                    verify(bad)
            with self.assertRaises(FileExistsError):
                summarize(raw, root)

    def test_invalid_condition_rate_missing_vad_and_nonfinite_input(self):
        for audio, condition, vad in (
            (self.audio(10), "unknown", None),
            (self.audio(10), "bounded_vad_5s", None),
            (Audio(array("f", [0]), 8000, "x"), "full_batch", None),
            (Audio(array("f", [float("nan")]), 16000, "x"), "full_batch", None),
        ):
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                probe.measure(audio, condition, adapter(), vad)

    def setup_fixture(self, root):
        config = root / "config.toml"
        config.write_text((ROOT / "configs/sensevoice_eval.toml").read_text())
        wav = root / "one.wav"
        write_wav(wav, array("h", [100, -100] * 8000))
        manifest = root / "manifest.jsonl"
        write_jsonl(
            manifest,
            [{"id": "one", "reference": "가", "audio_path": str(wav), "audio_sha256": digest(wav)}],
        )
        return config, manifest, wav

    def test_runner_one_load_common_warmup_close_and_no_source_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest, wav = self.setup_fixture(root)
            before = [digest(p) for p in (config, manifest, wav)]
            model = adapter()
            with (
                patch.object(probe, "create_adapter", return_value=model),
                patch.object(probe.subprocess, "check_output", return_value="fixture"),
            ):
                self.assertEqual(
                    probe.probe(
                        root, config, manifest, root / "out", "full_batch", root / "absent-vad"
                    ),
                    0,
                )
            worker = json.loads((root / "out/worker.json").read_text())
            self.assertEqual(worker["status"], "completed")
            self.assertEqual(worker["warmup"]["asr_successful"], 3)
            self.assertEqual(worker["warmup"]["vad_successful"], 0)
            model.load.assert_called_once()
            model.close.assert_called_once()
            self.assertEqual(model.transcribe.call_count, 4)
            self.assertEqual(before, [digest(p) for p in (config, manifest, wav)])

    def test_vad_runner_loads_once_warms_without_extra_asr_and_resets_each_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest, _ = self.setup_fixture(root)
            vad_path = root / "vad.onnx"
            model, probability = adapter(), Probability(1)
            probability.np = SimpleNamespace(asarray=lambda x, **kwargs: x, float32="mock")
            probability.metadata = lambda: {"reset_count": probability.reset_count}
            real_digest = probe.digest
            with (
                patch.object(probe, "create_adapter", return_value=model),
                patch.object(probe.subprocess, "check_output", return_value="fixture"),
                patch.object(
                    probe,
                    "digest",
                    side_effect=lambda p: probe.MODEL_SHA if p == vad_path else real_digest(p),
                ),
                patch(
                    "edge_asr.vad_probability.K2SileroProbability", return_value=probability
                ) as factory,
            ):
                self.assertEqual(
                    probe.probe(root, config, manifest, root / "out", "bounded_vad_5s", vad_path), 0
                )
            factory.assert_called_once_with(vad_path)
            model.load.assert_called_once()
            model.close.assert_called_once()
            self.assertEqual(model.transcribe.call_count, 4)
            worker = json.loads((root / "out/worker.json").read_text())
            self.assertEqual(worker["warmup"]["vad_successful"], 3)
            row = read_predictions(root / "out/predictions.jsonl")[0]
            self.assertEqual(row["segmented"]["vad_resets_during_utterance"], 1)

    def test_bad_configuration_and_hash_never_start_inference_or_overwrite_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest, wav = self.setup_fixture(root)
            config.write_text(config.read_text().replace("num_threads = 1", "num_threads = 2"))
            with self.assertRaises(ValueError):
                probe.probe(root, config, manifest, root / "out", "full_batch", root / "vad")
            self.assertFalse((root / "out").exists())
            config.write_text(config.read_text().replace("num_threads = 2", "num_threads = 1"))
            wav.write_bytes(wav.read_bytes() + b"changed")
            with (
                patch.object(probe, "create_adapter") as factory,
                patch.object(probe.subprocess, "check_output", return_value="fixture"),
            ):
                self.assertEqual(
                    probe.probe(root, config, manifest, root / "out", "full_batch", root / "vad"), 1
                )
                factory.assert_not_called()
            self.assertFalse(
                json.loads((root / "out/summary.json").read_text())["overall"]["complete"]
            )
            with self.assertRaises(FileExistsError):
                probe.probe(root, config, manifest, root / "out", "full_batch", root / "vad")


if __name__ == "__main__":
    unittest.main()
