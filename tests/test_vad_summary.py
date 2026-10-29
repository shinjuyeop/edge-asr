import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from edge_asr.tools.reports import vad as helper


def row(paced=False):
    return {
        "id": "one",
        "condition": "clean",
        "audio_sha256": "a" * 64,
        "max_segment_s": 5,
        "expected_speech": True,
        "speech_clip_fully_missed": False,
        "silence_or_noise_triggered": None,
        "segments": [{"raw_start_frame": 0, "raw_end_frame": 100}],
        "segments_exceeding_soft_max": 0,
        "vad_call_wall_s": 0.1,
        "vad_call_cpu_s": 0.001,
        "timing": {
            "native_accepted_frames": 1600,
            "processing_wall_s": 0.001,
            "input_lateness_s": [0.002] if paced else [],
            "paced": paced,
        },
        "endpoint_from_source_wav_end_audio_s": -0.1,
        "endpoint_from_source_wav_end_wall_s": -0.098 if paced else None,
        "resources": {"rss_observed_max_mib": 100},
    }


class VadSummaryTests(unittest.TestCase):
    def test_negative_proxy_and_unknown_metrics_are_preserved(self):
        a = helper.aggregate([row()])
        self.assertEqual(a["speech_files"], 1)
        self.assertEqual(a["endpoint_wav_end_audio_proxy_s"]["mean"], -0.1)
        self.assertIsNone(a["endpoint_wav_end_wall_proxy_s"]["mean"])
        self.assertIsNone(a["actual_acoustic_endpoint_s"])
        self.assertIsNone(a["input_drops"])
        negative = row()
        negative.update(
            expected_speech=False, speech_clip_fully_missed=None, silence_or_noise_triggered=True
        )
        a = helper.aggregate([row(), negative])
        self.assertEqual(a["negative_files_triggered"], 1)

    def fixture(self, root):
        data = root / "data"
        data.mkdir()
        base = root / "runs"
        base.mkdir()
        (data / "component-spans.json").write_text("{}")
        for paced in (False, True):
            for policy in ("identity", "rms26"):
                name = ("paced-" if paced else "unpaced-") + policy
                folder = base / name
                folder.mkdir()
                manifest = data / f"{name if paced else policy}.jsonl"
                manifest.write_text(
                    json.dumps(
                        {
                            "id": "one",
                            "reference": "말",
                            "audio_sha256": "a" * 64,
                            "condition": "clean",
                        }
                    )
                    + "\n"
                )
                worker = {
                    "status": "completed",
                    "input_manifest_sha256": helper.digest(manifest),
                    "model_sha256": "b" * 64,
                    "settings": {
                        "sample_rate": 16000,
                        "num_threads": 1,
                        "window_size": 512,
                        "threshold": 0.5,
                        "min_speech_duration": 0.1,
                        "min_silence_duration": 0.3,
                        "max_speech_duration_candidates": [5],
                    },
                }
                (folder / "worker.json").write_text(json.dumps(worker))
                (folder / "execution.json").write_text(
                    json.dumps(
                        {
                            "control_valid": True,
                            "source_commit": "fixture",
                            "before": {"temperature_mc": 50000},
                        }
                    )
                )
                (folder / "thermal.jsonl").write_text(
                    json.dumps({"temperature_mc": 50000, "current_limits": 0, "history_limits": 14})
                    + "\n"
                )
                (folder / "segments.jsonl").write_text(json.dumps(row(paced)) + "\n")
        return base, data

    def test_complete_result_checks_all_four_runs(self):
        with tempfile.TemporaryDirectory() as d:
            base, data = self.fixture(Path(d))
            value = helper.summarize(base, data)
            self.assertEqual(len(value["runs"]), 4)
            self.assertIsNone(value["vad_input_policy_approved"])

    def test_incomplete_or_changed_evidence_is_rejected(self):
        for error in ("thermal", "missing", "input", "model"):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as d:
                base, data = self.fixture(Path(d))
                folder = base / "unpaced-rms26"
                if error == "thermal":
                    (folder / "execution.json").write_text('{"control_valid":false}')
                elif error == "missing":
                    (folder / "segments.jsonl").write_text("")
                elif error == "input":
                    value = row()
                    value["audio_sha256"] = "c" * 64
                    (folder / "segments.jsonl").write_text(json.dumps(value) + "\n")
                else:
                    p = folder / "worker.json"
                    value = json.loads(p.read_text())
                    value["model_sha256"] = "c" * 64
                    p.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    helper.summarize(base, data)


if __name__ == "__main__":
    unittest.main()
