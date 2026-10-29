"""가중치 없는 확률/재귀 상태 mock으로 장시간 상태·유한 PCM·실패 처리를 검증한다."""

import math
import sys
import tempfile
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from edge_asr.audio_policy import preprocess
from edge_asr.dataset import Audio, pcm_bytes
from edge_asr.segmentation import SegmentPipeline
from edge_asr.tools.probes.annotated_vad import measure, probe
from edge_asr.vad_probability import BoundedProbabilityVad, K2SileroProbability


class Probability:
    def __init__(self, value=1):
        self.value = value
        self.resets = 0

    def reset(self):
        self.resets += 1
        self.calls = 0

    def __call__(self, samples):
        self.calls += 1
        return self.value(self.calls) if callable(self.value) else self.value


class Tensor:
    def __init__(self, values, shape):
        self.values, self.shape = list(values), shape
        self.size = len(self.values)
        self.nbytes = self.size * 4

    def reshape(self, *shape):
        return Tensor(self.values, (len(self.values),) if shape == (-1,) else shape)

    def __getitem__(self, index):
        return self.values[index]


class FakeNumpy:
    float32 = "float32"

    def zeros(self, shape, dtype):
        return Tensor([0] * math.prod(shape), shape)

    def asarray(self, samples, dtype):
        return Tensor(samples, (len(samples),))

    def isfinite(self, tensor):
        return SimpleNamespace(all=lambda: all(math.isfinite(x) for x in tensor.values))


class ProbabilityTests(unittest.TestCase):
    def test_model_factory_signature_state_carry_and_reset(self):
        class Session:
            def get_inputs(self):
                return [SimpleNamespace(name=n, type="tensor(float)") for n in ("x", "h", "c")]

            def get_outputs(self):
                return [
                    SimpleNamespace(name=n, type="tensor(float)")
                    for n in ("prob", "new_h", "new_c")
                ]

            def get_providers(self):
                return ["CPUExecutionProvider"]

            def run(self, names, feed):
                self.feed = feed
                return (
                    Tensor([0.8], (1,)),
                    Tensor([0.1] * 128, (2, 1, 64)),
                    Tensor([0.2] * 128, (2, 1, 64)),
                )

        session = Session()
        calls = []

        def factory(path, sess_options, providers):
            calls.append((sess_options, providers))
            return session

        ort = SimpleNamespace(
            SessionOptions=SimpleNamespace,
            InferenceSession=factory,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
            __version__="mock",
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"numpy": FakeNumpy(), "onnxruntime": ort}),
            patch(
                "edge_asr.vad_probability.digest",
                return_value=__import__("edge_asr.vad", fromlist=["MODEL_SHA"]).MODEL_SHA,
            ),
        ):
            model = K2SileroProbability(Path(directory) / "mock.onnx")
            self.assertEqual(model([0] * 512), 0.8)
            previous = model.h
            model([0] * 512)
            self.assertIs(session.feed["h"], previous)
            self.assertEqual(session.feed["x"].shape, (1, 512))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0].intra_op_num_threads, 1)
            self.assertEqual(calls[0][1], ["CPUExecutionProvider"])
            self.assertEqual(model.metadata()["recurrent_state_bytes"], 1024)
            model.reset()
            self.assertTrue(all(x == 0 for x in model.h.values))
            with self.assertRaises(ValueError):
                model([0] * 511)
            with self.assertRaises(ValueError):
                model([float("nan")] * 512)
            session.get_inputs = lambda: []
            with self.assertRaises(ValueError):
                K2SileroProbability(Path(directory) / "mock.onnx")

    def test_unapproved_model_rejected_before_native_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model"
            path.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "가중치"):
                K2SileroProbability(path)

    def test_minimum_speech_hysteresis_and_natural_end(self):
        probability = Probability(lambda i: 1 if i <= 5 else 0.4 if i <= 10 else 0)
        vad = BoundedProbabilityVad(probability)
        for _ in range(4):
            vad.accept_waveform([0] * 512)
        self.assertFalse(vad.is_speech_detected())
        vad.accept_waveform([0] * 512)
        self.assertTrue(vad.is_speech_detected())
        for _ in range(5):
            vad.accept_waveform([0] * 512)
        self.assertTrue(vad.is_speech_detected())
        for _ in range(11):
            vad.accept_waveform([0] * 512)
        self.assertFalse(vad.empty())
        self.assertFalse(vad.is_speech_detected())
        self.assertGreater(len(vad.front.samples), 0)
        self.assertIsInstance(vad.front.samples, range)
        with self.assertRaises(RuntimeError):
            vad.accept_waveform([0] * 512)
        vad.pop()
        self.assertTrue(vad.empty())

    def test_long_active_is_constant_storage_without_resets_or_audio_copies(self):
        probability = Probability()
        vad = BoundedProbabilityVad(probability)
        block = [0] * 512
        for _ in range(4096):
            vad.accept_waveform(block)
        self.assertGreater(vad.metadata()["active_span_peak_frames"], 16000 * 120)
        self.assertEqual(vad.metadata()["retained_audio_frames"], 0)
        self.assertEqual(probability.resets, 1)
        self.assertTrue(vad.empty())
        self.assertIsInstance(vad.current_segment.samples, range)
        self.assertEqual(vad.current_segment.end, 4096 * 512 - 1)
        vad.flush()
        self.assertEqual(vad.metadata()["queue_peak"], 1)
        self.assertEqual(vad.front.end, 4096 * 512)
        with self.assertRaises(ValueError):
            vad.flush()
        vad.pop()
        vad.reset()
        self.assertEqual(vad.fed, 0)

    def test_soft_limit_adjustment_ends_marginal_long_activity(self):
        vad = BoundedProbabilityVad(Probability(0.7))
        for _ in range(200):
            vad.accept_waveform([0] * 512)
            if not vad.empty():
                break
        self.assertFalse(vad.empty())
        self.assertLess(vad.front.end, 7 * 16000)
        # .8은 변경된 negative threshold .75보다 높아 계속 활성이다.
        vad = BoundedProbabilityVad(Probability(0.8))
        for _ in range(200):
            vad.accept_waveform([0] * 512)
        self.assertTrue(vad.empty())
        self.assertTrue(vad.is_speech_detected())

    def test_common_probe_records_bounded_pipeline_and_preserves_default_budget(self):
        import hashlib

        samples = array("f", [0.03] * 16000)
        want, meta = preprocess(samples, "identity")
        row = {
            "schema": "vad-annotation-v1",
            "id": "mock",
            "audio_path": "data/mock.wav",
            "audio_sha256": "a" * 64,
            "sample_rate_hz": 16000,
            "split": "development",
            "frames": len(samples),
            "category": "short",
            "annotation_status": "unreviewed",
            "target_interval_frames": None,
            "endpoint_reference_frame": None,
            "regions": [
                {"start_frame": 0, "end_frame": len(samples), "label": "ignore", "reason": "mock"}
            ],
            "expected_processed": {
                "identity": {
                    "pcm_sha256": hashlib.sha256(pcm_bytes(want)).hexdigest(),
                    "newly_clipped_samples": meta["newly_clipped_samples"],
                }
            },
        }
        audio = Audio(samples, 16000, "mock")
        for budget in (None, 30 * 16000):
            result = measure(
                BoundedProbabilityVad(Probability()),
                audio,
                row,
                "identity",
                list,
                native_budget_frames=budget,
            )
            self.assertEqual(result["status"], "completed", result["error"])
            self.assertEqual(result["pipeline"]["native_budget_frames"], budget)
            self.assertIsNone(result["score"]["speech_miss_fraction"])

    def test_unknown_backend_rejected_before_reading_assets(self):
        with self.assertRaisesRegex(ValueError, "백엔드"):
            probe(None, None, None, None, None, None, backend="unknown")

    def test_short_events_and_invalid_probability_do_not_make_segments(self):
        vad = BoundedProbabilityVad(Probability(lambda i: 1 if i <= 3 else 0))
        for _ in range(40):
            vad.accept_waveform([0] * 512)
        vad.flush()
        self.assertTrue(vad.empty())
        for invalid in (float("nan"), -1, 1.1):
            vad = BoundedProbabilityVad(Probability(invalid))
            with self.assertRaises(ValueError):
                vad.accept_waveform([0] * 512)
            self.assertEqual(vad.fed, 0)

    def test_pipeline_over_30_seconds_preserves_owned_pcm_and_all_caps(self):
        samples = array("f", [0.03] * (16000 * 35 + 137))
        want = preprocess(samples, "identity")[0]
        for maximum in (3, 5, 8):
            with self.subTest(maximum=maximum):
                probability = Probability()
                vad = BoundedProbabilityVad(probability)
                joined = array("h")
                last = 0
                events = []

                def emit(row, pcm):
                    nonlocal last
                    self.assertEqual(row["owned_start_frame"], last)
                    last = row["owned_end_frame"]
                    events.append(row)
                    joined.extend(pcm[row["owned_start_frame"] - row["context_start_frame"] :])
                    self.assertLessEqual(
                        row["owned_end_frame"] - row["owned_start_frame"], maximum * 16000
                    )
                    self.assertLessEqual(len(pcm), (maximum + 0.2) * 16000)

                pipe = SegmentPipeline(vad, "identity", maximum, emit, native_budget_frames=None)
                for offset in range(0, len(samples), 1600):
                    pipe.accept(
                        samples[offset : offset + 1600], final=offset + 1600 >= len(samples)
                    )
                result = pipe.finish()
                self.assertEqual(joined, want)
                self.assertIsNone(result["native_budget_frames"])
                self.assertFalse(result["native_budget_exhausted"])
                self.assertGreater(result["native_active_peak_frames"], 30 * 16000)
                self.assertLessEqual(pipe.ring.peak, pipe.ring.capacity)
                self.assertEqual(probability.resets, 2)
                self.assertTrue(events)
                self.assertTrue(all(not r["endpoint_eligible"] for r in events))

    def test_default_native_safety_cannot_be_disabled_for_other_backends(self):
        with self.assertRaisesRegex(ValueError, "축적"):
            SegmentPipeline(object(), "identity", 5, lambda r, p: None, native_budget_frames=None)


if __name__ == "__main__":
    unittest.main()
