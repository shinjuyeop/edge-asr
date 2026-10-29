import hashlib
import tempfile
import time
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace

from edge_asr.audio_policy import preprocess
from edge_asr.dataset import Audio
from edge_asr.segmentation import PcmRing, SegmentPipeline, SegmentReplayAdapter, pcm_bytes
from edge_asr.streaming import run_paced
from edge_asr.vad_probe import source_metrics

ROOT = Path(__file__).resolve().parents[1]
from edge_asr.tools.probes.segments import validate_capture


class Vad:
    """native wrapper와 같은 절대 프레임/flush 의미. 음성 분류 정확도의 mock이 아님."""

    def __init__(self, voice=lambda index: True, delay=0):
        self.voice, self.delay, self.resets = voice, delay, 0
        self.reset()

    def reset(self):
        self.resets += 1
        self.frame = 0
        self.start = None
        self.queue = []
        self.blocks = 0
        self.flushes = 0

    def accept_waveform(self, block):
        if self.delay:
            time.sleep(self.delay)
        begin = self.frame
        self.frame += len(block)
        self.blocks += 1
        if self.voice(self.blocks):
            if self.start is None:
                self.start = begin
        elif self.start is not None:
            self.queue.append(SimpleNamespace(start=self.start, samples=[0] * (begin - self.start)))
            self.start = None

    def is_speech_detected(self):
        return self.start is not None

    @property
    def current_segment(self):
        return SimpleNamespace(start=self.start)

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0)

    def flush(self):
        self.flushes += 1
        if self.start is not None:
            self.queue.append(
                SimpleNamespace(start=self.start, samples=[0] * (self.frame - self.start))
            )
            self.start = None


def run(samples, maximum=3, policy="identity", vad=None):
    records = []
    detector = vad or Vad()
    pipeline = SegmentPipeline(detector, policy, maximum, lambda r, p: records.append((r, p)))
    for start in range(0, len(samples), 1600):
        pipeline.accept(samples[start : start + 1600], final=start + 1600 >= len(samples))
    pipeline.finish()
    return pipeline, records


class SegmentTests(unittest.TestCase):
    def test_ring_is_bounded_and_rejects_lost_or_discontinuous_ranges(self):
        ring = PcmRing(5)
        ring.append(0, array("h", [1, 2, 3]))
        ring.append(3, array("h", [4, 5, 6]))
        self.assertEqual(list(ring.read(1, 6)), [2, 3, 4, 5, 6])
        self.assertEqual(ring.peak, 5)
        for a, b in [(0, 2), (1, 7), (3, 2)]:
            with self.assertRaises(ValueError):
                ring.read(a, b)
        with self.assertRaises(ValueError):
            ring.append(7, array("h", [1]))

    def test_sustained_voice_emits_before_eof_with_no_owned_frame_loss(self):
        samples = array("f", ((i % 200 - 100) / 32768 for i in range(16000 * 13 + 137)))
        for maximum in (3, 5, 8):
            with self.subTest(maximum=maximum):
                vad = Vad()
                p, rows = run(samples, maximum, vad=vad)
                self.assertGreater(p.hard_emissions, 0)
                self.assertEqual(vad.resets, 2)
                self.assertEqual(
                    vad.flushes, 1
                )  # EOF 한 번만. 활성 flush 후 음수 native 구간 재발 방지.
                owned = array("h")
                last = 0
                for row, pcm in rows:
                    self.assertEqual(row["owned_start_frame"], last)
                    last = row["owned_end_frame"]
                    self.assertLessEqual(last - row["owned_start_frame"], maximum * 16000)
                    self.assertLessEqual(len(pcm), (maximum + 0.2) * 16000)
                    owned.extend(pcm[row["owned_start_frame"] - row["context_start_frame"] :])
                    if row["reason"] == "hard_limit":
                        self.assertLess(row["emitted_at_audio_frame"], len(samples))
                        self.assertEqual(last - row["owned_start_frame"], maximum * 16000)
                        self.assertLessEqual(row["emitted_at_audio_frame"] - last, 511)
                        self.assertFalse(row["endpoint_eligible"])
                self.assertEqual(last, len(samples))
                self.assertEqual(owned, preprocess(samples, "identity")[0])
                self.assertLessEqual(p.ring.peak, p.ring.capacity)
                self.assertLessEqual(p.native_active_peak, p.native_budget)
                self.assertLessEqual(p.pending_peak, 1600 + 511)

    def test_silence_and_natural_end_are_separate_from_hard_limit(self):
        p, rows = run(array("f", [0] * 5000), vad=Vad(lambda i: False))
        self.assertEqual(rows, [])
        p, rows = run(array("f", [0.02] * 6000), vad=Vad(lambda i: i in (2, 3, 7, 8)))
        self.assertEqual(
            [(r["owned_start_frame"], r["owned_end_frame"]) for r, _ in rows],
            [(512, 1536), (3072, 4096)],
        )
        self.assertTrue(all(r["reason"] == "vad_end" for r, _ in rows))

    def test_rms_state_rechunk_tail_and_payload_match_common_pcm(self):
        samples = array("f", [0] * 1600 + [0.002, -0.002] * 6500 + [0] * 177)
        p, rows = run(samples, policy="rms26")
        pcm, _ = preprocess(samples, "rms26")
        self.assertEqual(
            p.metadata()["processed_pcm_sha256"], hashlib.sha256(pcm_bytes(pcm)).hexdigest()
        )
        self.assertEqual(p.fed, ((len(samples) + 511) // 512) * 512)
        for r, payload in rows:
            self.assertEqual(payload, pcm[r["context_start_frame"] : r["context_end_frame"]])
        with self.assertRaises(ValueError):
            p.finish()
        with self.assertRaises(ValueError):
            p.accept(array("f", [0] * 1600))

    def test_invalid_configuration_and_native_intervals_fail_closed(self):
        for cap in (0, 1, 3.0, True, 9):
            with self.assertRaises(ValueError):
                SegmentPipeline(Vad(), "identity", cap, lambda *a: None)
        p = SegmentPipeline(Vad(), "identity", 3, lambda *a: None)
        with self.assertRaises(ValueError):
            p.finish()
        with self.assertRaises(ValueError):
            p.accept(array("f", [0] * 3))

        class Invalid(Vad):
            @property
            def current_segment(self):
                return SimpleNamespace(start=-1)

        with self.assertRaises(ValueError):
            run(array("f", [0] * 1600), vad=Invalid())

    def test_native_active_budget_stops_before_buffer_overflow(self):
        p = SegmentPipeline(Vad(), "identity", 3, lambda *a: None, native_budget_frames=48000 + 512)
        with self.assertRaisesRegex(RuntimeError, "native_active_budget_exhausted"):
            for _ in range(45):
                p.accept(array("f", [0.01] * 1600))
        self.assertTrue(p.native_budget_exhausted)
        self.assertLessEqual(p.fed, p.native_budget)
        self.assertEqual(p.hard_emissions, 1)
        self.assertFalse(p.closed)

    def test_native_end_retraction_is_recorded_without_retracting_owned_pcm(self):
        class Retracting(Vad):
            def accept_waveform(self, block):
                super().accept_waveform(block)
                if self.blocks == 95:
                    self.queue = [SimpleNamespace(start=0, samples=[0] * 44000)]
                    self.start = None

        p, rows = run(array("f", [0.02] * 50000), vad=Retracting(lambda i: i < 95))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0]["owned_end_frame"], 48000)
        self.assertEqual(p.committed_tail_overhang, 4000)
        metrics = source_metrics([rows[0][0]], 0, 48000, expected_speech=True)
        self.assertIsNone(metrics["endpoint_from_source_wav_end_audio_s"])

    def test_independent_paced_capture_and_overload_are_not_false_success(self):
        for delay in (0, 0.09):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as d:
                samples = array("f", [0.002] * 9767)
                adapter = SegmentReplayAdapter(
                    lambda: SegmentPipeline(Vad(delay=delay), "identity", 3, lambda *a: None)
                )
                telemetry = {}
                run_paced(
                    adapter,
                    Audio(samples, 16000, "mock"),
                    {
                        "chunk_ms": 100,
                        "queue_capacity_chunks": 1,
                        "drop_policy": "drop_newest",
                        "drain_timeout_s": 3,
                    },
                    Path(d) / "events",
                    telemetry,
                )
                pcm, info = preprocess(samples, "identity")
                expected = {
                    "padded_frames": len(samples),
                    "expected_processed": {
                        "identity": {
                            "pcm_sha256": hashlib.sha256(pcm_bytes(pcm)).hexdigest(),
                            "newly_clipped_samples": 0,
                        }
                    },
                }
                if delay:
                    self.assertGreater(telemetry["dropped_chunks"], 0)
                    with self.assertRaises(RuntimeError):
                        validate_capture(
                            adapter.pipeline.metadata(), telemetry, expected, "identity"
                        )
                else:
                    validate_capture(adapter.pipeline.metadata(), telemetry, expected, "identity")
                    self.assertEqual(telemetry["dropped_chunks"], 0)
                    self.assertLessEqual(telemetry["backlog"]["event_max_chunks"], 1)
                    self.assertIsNone(telemetry["first_partial_from_audio_start_s"])


if __name__ == "__main__":
    unittest.main()
