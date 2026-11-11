"""실제 모델 없는 연속 입력·인과성·유한 큐·부분/확정 지연 계약 검사."""

import json
import tempfile
import time
import unittest
from array import array
from pathlib import Path
from threading import Event
from unittest.mock import patch

from edge_asr.artifacts import digest
from edge_asr.dataset import prepare_track, write_wav
from edge_asr.qwen.realtime.replay import run
from edge_asr.qwen.realtime.session import CaptionSession
from edge_asr.qwen.realtime.worker import DecodeWorker
from edge_asr.streaming import run_paced
from edge_asr.vad_probability import BoundedProbabilityVad


class Probability:
    def reset(self):
        pass

    def __call__(self, samples):
        return 1.0 if any(samples) else 0.0


class Adapter:
    last_result_metadata = None

    def __init__(self):
        self.entered, self.release = Event(), Event()
        self.calls = []

    def transcribe(self, audio):
        self.calls.append(list(audio.samples))
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError("fixture wait")
        return "전사"


class RealtimeTests(unittest.TestCase):
    def test_first_partial_delay_is_independent_of_later_update_interval(self):
        adapter, events = Adapter(), []
        adapter.release.set()
        session = CaptionSession(
            adapter,
            BoundedProbabilityVad(Probability()),
            16000,
            events.append,
            {"t0_monotonic_ns": time.perf_counter_ns()},
            partial_after_s=0.2,
            partial_interval_s=0.4,
        )
        try:
            for start in range(0, 16000, 1600):
                session.accept_at(session, array("f", [0.25] * 1600), start, start + 1600)
            session.finish_stream(session)
            submitted = [e for e in events if e["type"] == "submitted" and e["kind"] == "partial"]
            self.assertTrue(submitted)
            first = submitted[0]
            self.assertGreaterEqual((first["end_frame"] - first["start_frame"]) / 16000, 0.2)
            self.assertLess((first["end_frame"] - first["start_frame"]) / 16000, 0.3)
            for a, b in zip(submitted, submitted[1:]):
                self.assertGreaterEqual((b["end_frame"] - a["end_frame"]) / 16000, 0.4)
        finally:
            session.close()

    def test_partial_control_preserves_final_pcm_and_segmentation(self):
        payloads, segments = [], []
        for partials in (True, False):
            adapter, events = Adapter(), []
            adapter.release.set()
            session = CaptionSession(
                adapter,
                BoundedProbabilityVad(Probability()),
                16000,
                events.append,
                {"t0_monotonic_ns": time.perf_counter_ns()},
                partial_after_s=0.2,
                partial_interval_s=0.2,
                partials=partials,
            )
            try:
                for start in range(0, 16000, 1600):
                    session.accept_at(session, array("f", [0.25] * 1600), start, start + 1600)
                session.finish_stream(session)
                finals = [e for e in events if e["type"] == "submitted" and e["kind"] == "final"]
                payloads.append([e["payload_float32_sha256"] for e in finals])
                segments.append(
                    [(s["owned_start_frame"], s["owned_end_frame"]) for s in session.segments]
                )
                if not partials:
                    self.assertFalse(any(e.get("kind") == "partial" for e in events))
            finally:
                session.close()
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(segments[0], segments[1])

    def test_zero_policy_records_silence_and_keeps_one_lsb_audio(self):
        class AlwaysSpeech(Probability):
            def __call__(self, samples):
                return 1.0

        for value in (0.0, 1 / 32768):
            adapter, events = Adapter(), []
            adapter.release.set()
            session = CaptionSession(
                adapter,
                BoundedProbabilityVad(AlwaysSpeech()),
                16000,
                events.append,
                {"t0_monotonic_ns": time.perf_counter_ns()},
                partials=False,
                silence_policy="skip-zero",
            )
            try:
                for start in range(0, 16000, 1600):
                    session.accept_at(session, array("f", [value] * 1600), start, start + 1600)
                text = session.finish_stream(session)
                self.assertEqual(bool(session.skipped), value == 0)
                self.assertEqual(bool(adapter.calls), value != 0)
                self.assertEqual(text, "" if value == 0 else "전사")
                if value == 0:
                    self.assertEqual(session.skipped[0]["reason"], "exact_zero_pcm")
                    self.assertFalse(session.results)
                with self.assertRaises(ValueError):
                    session._offer("final", 99, 16000, 17600, array("h", [0] * 1600))
            finally:
                session.close()

    def test_runner_preserves_failed_calls_and_refuses_existing_output(self):
        class Loaded(Adapter):
            model_files = {}
            metadata = {"backend": "mock"}
            supports_token_stream = True

            def __init__(self, fail):
                super().__init__()
                self.fail, self.count, self.closed = fail, 0, False

            def load(self):
                pass

            def close(self):
                self.closed = True

            def transcribe(self, audio):
                self.count += 1
                if self.fail and self.count > 4:
                    raise ValueError("fixture replay failure")
                return "전사"

            def transcribe_stream(self, audio, emit):
                emit(
                    {
                        "hypothesis": "전",
                        "token_index": 1,
                        "native_elapsed_s": 0.01,
                        "piece_hex": "전".encode().hex(),
                    }
                )
                return self.transcribe(audio)

        root = Path(__file__).resolve().parents[2]
        for config, fail, tokens in (
            (config, fail, tokens)
            for config in ("onnx", "gguf")
            for fail in (False, True)
            for tokens in ((False, True) if config == "gguf" else (False,))
        ):
            with self.subTest(config=config, fail=fail), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                wav = directory / "one.wav"
                write_wav(wav, [1000] * 12800 + [0] * 8000)
                manifest = directory / "manifest.jsonl"
                manifest.write_text(
                    json.dumps(
                        {
                            "id": "one",
                            "reference": "전사",
                            "audio_path": str(wav),
                            "audio_sha256": digest(wav),
                        }
                    )
                )
                vad_model = directory / "vad.onnx"
                vad_model.write_bytes(b"fixture")
                adapter = Loaded(fail)
                output = directory / "run"
                with patch("edge_asr.qwen.realtime.replay.limits_verified", return_value=True):
                    info = run(
                        root,
                        root / f"configs/{config}.toml",
                        manifest,
                        output,
                        vad_model,
                        adapter_factory=lambda *_: adapter,
                        vad_factory=lambda _: BoundedProbabilityVad(Probability()),
                        gap_s=0,
                        vad_max_speech_s=30,
                        partial_after_s=0.2,
                        partial_interval_s=0.2,
                        token_stream=tokens,
                    )
                    self.assertEqual(info["status"], "failed" if fail else "completed")
                    self.assertEqual(
                        info["config"]["model"]["family"],
                        "qwen3_asr" if config == "onnx" else "qwen3_gguf",
                    )
                    self.assertTrue(info["telemetry"]["complete"])
                    self.assertTrue(adapter.closed)
                    self.assertIsNone(info["engine_ttft_s"])
                    self.assertEqual(info["final_transcript_score"]["cer"], 1.0 if fail else 0.0)
                    self.assertEqual(info["failed_calls"] > 0, fail)
                    self.assertTrue((output / "jobs.jsonl").exists())
                    self.assertEqual(bool(info["token_timings"]), tokens)
                    if tokens:
                        self.assertLessEqual(
                            info["first_visible_caption_from_track_start_s"],
                            info["first_caption_from_track_start_s"] or float("inf"),
                        )
                    before = digest(output / "worker.json")
                    with self.assertRaises(FileExistsError):
                        run(root, root / f"configs/{config}.toml", manifest, output, vad_model)
                    self.assertEqual(before, digest(output / "worker.json"))

    def test_generating_final_remains_provisional_and_is_cleared_on_failure(self):
        class Streaming(Adapter):
            supports_token_stream = True

            def transcribe_stream(self, audio, emit):
                emit(
                    {
                        "hypothesis": "한",
                        "token_index": 1,
                        "native_elapsed_s": 0.01,
                        "piece_hex": "한".encode().hex(),
                    }
                )
                self.entered.set()
                if not self.release.wait(3):
                    raise TimeoutError("fixture token wait")
                if self.fail:
                    raise ValueError("fixture terminal failure")
                return "한 글"

        for fail in (False, True):
            adapter, events = Streaming(), []
            adapter.fail = fail
            session = CaptionSession(
                adapter,
                BoundedProbabilityVad(Probability()),
                20800,
                events.append,
                {"t0_monotonic_ns": time.perf_counter_ns()},
                partials=False,
                token_stream=True,
            )
            try:
                for start in range(0, 20800, 1600):
                    value = 0.25 if start < 12800 else 0.0
                    session.accept_at(session, array("f", [value] * 1600), start, start + 1600)
                self.assertTrue(adapter.entered.wait(2))
                self.assertEqual(session.position, 20800)
                self.assertEqual(session.text(), "한")
                self.assertEqual(session.text(final_only=True), "")
                adapter.release.set()
                text = session.finish_stream(session)
                self.assertEqual(text, "" if fail else "한 글")
                self.assertIsNone(session.partial)
                self.assertEqual(session.results[0]["status"], "failed" if fail else "success")
                self.assertEqual(
                    [e["status"] for e in events if e["type"] == "token"], ["provisional"]
                )
            finally:
                adapter.release.set()
                session.close()

    def test_one_inflight_latest_partial_and_finals_first(self):
        adapter, events = Adapter(), []
        worker = DecodeWorker(adapter, events.append, capacity=1)
        self.addCleanup(worker.abort)
        pcm = array("f", [0.1] * 1600)
        worker.submit("partial", 0, 0, 1600, pcm, 1600)
        self.assertTrue(adapter.entered.wait(2))
        worker.submit("partial", 0, 0, 1600, pcm, 1600)
        worker.submit("partial", 0, 0, 1600, pcm, 1600)
        worker.submit("final", 0, 0, 1600, pcm, 1600)
        with self.assertRaisesRegex(RuntimeError, "final_queue_full"):
            worker.submit("final", 1, 1600, 3200, pcm, 3200)
        with self.assertRaises(ValueError):
            worker.submit("partial", 1, 1600, 3200, pcm, 3000)
        adapter.release.set()
        worker.finish(3)
        self.assertEqual([e["kind"] for e in events if e["type"] == "result"], ["partial", "final"])
        self.assertEqual(sum(e["type"] == "coalesced" for e in events), 2)
        self.assertLessEqual(worker.queue_peak, 2)

    def test_decode_failure_is_recorded_and_does_not_erase_final(self):
        class Failing(Adapter):
            def transcribe(self, audio):
                if len(audio.samples) == 320:
                    raise ValueError("fixture short audio")
                return "최종"

        events = []
        worker = DecodeWorker(Failing(), events.append)
        self.addCleanup(worker.abort)
        worker.submit("final", 0, 0, 320, array("f", [0] * 320), 320)
        worker.submit("final", 1, 320, 960, array("f", [0] * 640), 960)
        worker.finish(3)
        results = [e for e in events if e["type"] == "result"]
        self.assertEqual([r["status"] for r in results], ["failed", "success"])
        self.assertEqual(results[0]["error"]["type"], "ValueError")

    def test_input_continues_while_decode_is_blocked_and_gap_is_rejected(self):
        adapter, events = Adapter(), []
        telemetry = {"t0_monotonic_ns": time.perf_counter_ns()}
        session = CaptionSession(
            adapter,
            BoundedProbabilityVad(Probability()),
            16000,
            events.append,
            telemetry,
            partial_after_s=0.2,
            partial_interval_s=0.1,
        )
        self.addCleanup(session.close)
        for start in range(0, 8000, 1600):
            session.accept_at(session, array("f", [0.25] * 1600), start, start + 1600)
        self.assertTrue(adapter.entered.wait(2))
        self.assertEqual(session.position, 8000)
        self.assertTrue(all(e["end_frame"] <= e["available_frame"] for e in events))
        self.assertTrue(all(len(pcm) <= 8000 for pcm in adapter.calls))
        with self.assertRaisesRegex(RuntimeError, "capture_gap"):
            session.accept_at(session, array("f", [0] * 1600), 9600, 11200)
        adapter.release.set()

    def test_real_paced_producer_and_partial_before_audio_end(self):
        from edge_asr.dataset import Audio

        adapter, events, telemetry = Adapter(), [], {}
        adapter.release.set()
        pcm = array("f", [0.25] * 12800 + [0] * 8000)
        session = CaptionSession(
            adapter,
            BoundedProbabilityVad(Probability()),
            len(pcm),
            events.append,
            telemetry,
            partial_after_s=0.2,
            partial_interval_s=0.2,
        )
        self.addCleanup(session.close)
        with tempfile.TemporaryDirectory() as temporary:
            text = run_paced(
                session,
                Audio(pcm, 16000, "fixture"),
                {"chunk_ms": 100, "queue_capacity_chunks": 10, "drain_timeout_s": 5},
                Path(temporary) / "stream",
                telemetry,
                chunk_handler=session.accept_at,
            )
        self.assertEqual(text, "전사")
        self.assertTrue(telemetry["complete"])
        self.assertEqual(telemetry["dropped_chunks"], 0)
        partials = [e for e in events if e["type"] == "result" and e["kind"] == "partial"]
        self.assertTrue(partials)
        self.assertLess(partials[0]["published_s"], 0.8)
        self.assertEqual(session.results[-1]["kind"], "final")

    def test_track_gaps_preserve_old_default_coordinates_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_wav(root / "one.wav", [1000] * 1600)
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "id": str(i),
                            "reference": "전사",
                            "audio_path": "one.wav",
                            "audio_sha256": digest(root / "one.wav"),
                        }
                    )
                    for i in range(2)
                )
            )
            original, sources, manifest_sha = prepare_track(root, manifest)
            gapped, separated, same_sha = prepare_track(root, manifest, gap_s=1)
            self.assertEqual((len(original.samples), sources[1]["start"]), (3200, 1600))
            self.assertEqual((len(gapped.samples), separated[1]["start"]), (35200, 17600))
            self.assertEqual(original.samples[:1600], gapped.samples[:1600])
            self.assertFalse(any(gapped.samples[1600:17600]))
            self.assertEqual(manifest_sha, same_sha)
            with self.assertRaises(ValueError):
                prepare_track(root, manifest, gap_s=float("nan"))


if __name__ == "__main__":
    unittest.main()
