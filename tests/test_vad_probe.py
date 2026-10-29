import json
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from edge_asr.vad_probe import collect_segments, source_metrics


class FakeVad:
    def reset(self):
        self.steps = 0
        self.queue = []

    def accept_waveform(self, block):
        self.steps += 1
        if self.steps == 2:
            self.queue.append(SimpleNamespace(start=10, samples=[0] * 600))
        if self.steps == 3:
            self.queue.append(SimpleNamespace(start=500, samples=[0] * 700))

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0)

    def flush(self):
        pass


class VadProbeTests(unittest.TestCase):
    def test_paced_clock_is_measured_separately_from_audio_timeline(self):
        class Clock:
            now = 0.0

            def clock(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        clock = Clock()
        timing = {}
        rows = collect_segments(
            FakeVad(),
            array("f", [0] * 1600),
            lambda x: x,
            paced=True,
            telemetry=timing,
            clock=clock.clock,
            sleep=clock.sleep,
        )
        self.assertAlmostEqual(timing["elapsed_wall_s"], 0.1)
        self.assertEqual(timing["input_lateness_s"], [0, 0, 0, 0])
        m = source_metrics(rows, 100, 1300, expected_speech=True)
        self.assertAlmostEqual(m["endpoint_from_source_wav_end_wall_s"], (1536 - 1300) / 16000)
        self.assertIsNone(m["acoustic_endpoint_delay_s"])
        self.assertIsNone(timing["input_drops"])

    def test_native_configuration_contract_with_mock_runtime_only(self):
        from edge_asr.tools.probes import vad as module

        configs = []

        def detector(config, buffer_size_in_seconds):
            configs.append((config, buffer_size_in_seconds))
            return FakeVad()

        fake = SimpleNamespace(
            VadModelConfig=lambda: SimpleNamespace(silero_vad=SimpleNamespace()),
            VoiceActivityDetector=detector,
        )
        numpy = SimpleNamespace(float32="mock-float32", asarray=lambda a, dtype: a)
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sherpa_onnx": fake, "numpy": numpy}),
            patch.object(module, "runtime_info", return_value={"status": "mock"}),
        ):
            directory = Path(d)
            output = directory / "out"
            output.mkdir()
            with self.assertRaises(ValueError):
                module.probe(
                    directory, directory / "missing.onnx", directory / "missing.jsonl", output
                )
            model = directory / "mock.onnx"
            model.write_bytes(b"mock model contract only")
            wav = directory / "test.wav"
            with wave.open(str(wav), "wb") as handle:
                handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                handle.writeframes(b"\0\0" * 1600)
            manifest = directory / "manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "id": "fixture",
                        "audio_path": str(wav),
                        "reference": "",
                        "audio_sha256": module.digest(wav),
                    }
                )
            )
            module.probe(directory, model, manifest, output)
            self.assertEqual(
                json.loads((output / "worker.json").read_text())["status"], "completed"
            )
            self.assertEqual([c.silero_vad.max_speech_duration for c, _ in configs], [3, 5, 8])
            self.assertTrue(
                all(
                    c.num_threads == 1
                    and c.sample_rate == 16000
                    and c.silero_vad.min_silence_duration == 0.3
                    for c, _ in configs
                )
            )
            self.assertEqual(len((output / "segments.jsonl").read_text().splitlines()), 3)
            with self.assertRaises(ValueError):
                module.probe(directory, model, manifest, output)

    def test_intervals_are_owned_once_with_separate_context(self):
        rows = collect_segments(FakeVad(), array("f", [0] * 1400), lambda x: x)
        self.assertEqual(
            [(r["owned_start_frame"], r["owned_end_frame"]) for r in rows], [(10, 610), (610, 1200)]
        )
        self.assertEqual(rows[1]["overlap_removed_frames"], 110)
        self.assertEqual(rows[1]["context_start_frame"], 0)
        self.assertEqual(rows[-1]["emitted_at_audio_frame"], 1400)

    def test_proxy_never_claims_true_acoustic_or_paced_latency(self):
        rows = collect_segments(FakeVad(), array("f", [0] * 1600), lambda x: x)
        metrics = source_metrics(rows, 100, 1300, expected_speech=True)
        self.assertFalse(metrics["speech_clip_fully_missed"])
        self.assertEqual(metrics["endpoint_from_source_wav_end_audio_s"], (1536 - 1300) / 16000)
        self.assertIsNone(metrics["acoustic_endpoint_delay_s"])
        self.assertIsNone(metrics["paced_wall_endpoint_delay_s"])
        rows[-1]["after_flush"] = True
        self.assertIsNone(
            source_metrics(rows, 100, 1300, expected_speech=True)[
                "endpoint_from_source_wav_end_audio_s"
            ]
        )

    def test_empty_detection_and_noise_triggers_are_distinct(self):
        self.assertTrue(
            source_metrics([], 10, 100, expected_speech=True)["speech_clip_fully_missed"]
        )
        self.assertFalse(
            source_metrics([], 10, 100, expected_speech=False)["silence_or_noise_triggered"]
        )
        rows = collect_segments(FakeVad(), array("f", [0] * 1400), lambda x: x)
        self.assertTrue(
            source_metrics(rows, 10, 100, expected_speech=False)["silence_or_noise_triggered"]
        )

    def test_invalid_inputs_and_backend_ranges_fail(self):
        with self.assertRaises(ValueError):
            collect_segments(FakeVad(), [], lambda x: x)

        class Invalid(FakeVad):
            def accept_waveform(self, block):
                self.queue.append(SimpleNamespace(start=-1, samples=[0]))

        with self.assertRaises(ValueError):
            collect_segments(Invalid(), array("f", [0] * 512), lambda x: x)


if __name__ == "__main__":
    unittest.main()
