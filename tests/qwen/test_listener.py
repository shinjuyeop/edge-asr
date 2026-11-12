"""실제 모델 없이 청취 시간축·실패·음원 보존·HTTP 경계를 검증한다."""

import json
import tempfile
import threading
import time
import unittest
from array import array
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.dataset import Audio, prepare_track, write_wav
from edge_asr.qwen.viewer.analysis import compact_event, source_metrics
from edge_asr.qwen.viewer.runs import Library, Run
from edge_asr.qwen.viewer.server import handler
from edge_asr.streaming import run_paced


def event(job, start, end, published, text, *, kind="final", status="success", type="result"):
    return {
        "type": type,
        "job_id": job,
        "segment_id": job,
        "start_frame": start,
        "end_frame": end,
        "published_s": published,
        "hypothesis": text,
        "displayed": True,
        "kind": kind,
        "status": status,
    }


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "run"
        self.output.mkdir()
        wav = self.root / "a.wav"
        write_wav(wav, [1200] * 3200)
        self.manifest = self.root / "manifest.jsonl"
        write_jsonl(
            self.manifest,
            [
                {
                    "id": "a",
                    "audio_path": str(wav),
                    "reference": "가나다라",
                    "audio_sha256": digest(wav),
                }
            ],
        )
        audio, sources, _ = prepare_track(self.root, self.manifest)
        self.worker = {
            "kind": "qwen3_paced_partial_replay",
            "status": "completed",
            "sources": sources,
            "audio": audio.metadata(),
            "settings": {"gap_s": 0},
            "telemetry": {"complete": True, "dropped_chunks": 0},
            "final_transcript_score": {"cer": 0.0},
        }
        write_json(self.output / "worker.json", self.worker)
        self.library = Library(self.root, [self.output], [], [self.manifest])
        self.run = next(iter(self.library.runs.values()))

    def test_failed_tokens_and_cross_source_captions_are_not_unique_firsts(self):
        sources = [{"id": "a", "start": 0, "end": 16000}, {"id": "b", "start": 16000, "end": 32000}]
        events = [
            event(0, 0, 8000, 0.7, "거짓", type="token", status="provisional"),
            event(0, 0, 8000, 0.9, None, status="failed"),
            event(1, 0, 18000, 1.8, "경계결과"),
            event(2, 18000, 32000, 2.2, "가", type="token", status="provisional"),
            event(2, 18000, 32000, 2.5, "가나다라"),
        ]
        result = source_metrics(sources, events)
        self.assertIsNone(result["rows"][0]["first_s"])
        self.assertAlmostEqual(result["rows"][1]["first_s"], 1.2)
        self.assertAlmostEqual(result["rows"][1]["first4_s"], 1.5)
        self.assertTrue(result["rows"][0]["ambiguous_final"])
        self.assertEqual(result["first_s"]["n"], 1)
        self.assertIsNone(result["first_s"]["p95"])

    def test_firsts_ignore_suppressed_prefix_and_keep_failure_outcomes(self):
        a = event(0, 0, 3200, 0.3, "가")
        hidden = event(1, 0, 3200, 0.2, "가나다라", type="token", status="provisional")
        hidden["displayed"] = False
        end = event(1, 0, 3200, 0.8, "가나다라")
        result = source_metrics(self.worker["sources"], [a, hidden, end])
        self.assertEqual(result["rows"][0]["first_s"], 0.3)
        self.assertEqual(result["rows"][0]["first4_s"], 0.8)

    def test_partial_line_is_read_once_after_completion(self):
        row = event(0, 0, 3200, 0.3, "가나다라")
        raw = json.dumps(row).encode()
        path = self.output / "jobs.jsonl"
        path.write_bytes(raw[:15])
        self.assertEqual(self.run.snapshot()["cursor"], 0)
        with path.open("ab") as f:
            f.write(raw[15:] + b"\n")
        self.assertEqual(len(self.run.snapshot()["events"]), 1)
        self.assertEqual(self.run.snapshot(1)["events"], [])
        self.assertEqual(self.run.offset, path.stat().st_size)

    def test_incomplete_score_is_not_presented_as_full_cer(self):
        self.worker.update(status="failed", telemetry={"complete": False})
        write_json(self.output / "worker.json", self.worker)
        self.assertIsNone(self.run.snapshot()["score"])
        self.worker["telemetry"]["complete"] = True
        write_json(self.output / "worker.json", self.worker)
        self.assertEqual(self.run.snapshot()["score"]["cer"], 0)

    def test_guard_interruption_is_terminal_even_if_worker_snapshot_is_stale(self):
        self.worker["status"] = "replaying"
        self.worker["telemetry"] = {}
        write_json(self.output / "worker.json", self.worker)
        write_json(
            self.output / "execution.json", {"status": "blocked", "finished_at_utc": "fixture"}
        )
        state = self.run.snapshot()
        self.assertTrue(state["terminal"])
        self.assertEqual(state["status"], "interrupted")
        self.assertIsNone(state["score"])

    def test_reconstructed_audio_matches_original_and_does_not_touch_run(self):
        before = digest(self.output / "worker.json")
        audio = self.library.audio(self.run)
        self.assertTrue(audio.is_file())
        self.assertEqual(digest(audio), digest(self.root / "a.wav"))
        self.assertEqual(before, digest(self.output / "worker.json"))

    def test_wrong_pcm_and_mutated_cache_are_rejected(self):
        self.worker["audio"]["sha256"] = "f" * 64
        write_json(self.output / "worker.json", self.worker)
        with self.assertRaisesRegex(ValueError, "PCM"):
            self.library.audio(self.run)
        audio, _, _ = prepare_track(self.root, self.manifest)
        self.worker["audio"] = audio.metadata()
        write_json(self.output / "worker.json", self.worker)
        target = self.library.audio(self.run)
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "해시"):
            self.library.audio(Run(self.output))

    def test_start_requires_ready_and_does_not_overwrite_previous_schedule(self):
        with self.assertRaises(ValueError):
            self.library.start(self.run.id)
        self.library.active_id = self.run.id
        self.library.process = Mock()
        self.library.process.poll.return_value = None
        self.worker["status"] = "ready"
        write_json(self.output / "worker.json", self.worker)
        value = self.library.start(self.run.id)
        self.assertGreater(value["start_monotonic_s"], time.perf_counter())
        before = digest(self.output / "viewer-start.json")
        self.assertEqual(value, self.library.start(self.run.id))
        self.assertEqual(before, digest(self.output / "viewer-start.json"))

    def test_http_no_arbitrary_path_and_cross_origin_launch(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.library))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(url + "/api/clock") as response:
            value = json.load(response)
        self.assertLessEqual(value["received_s"], value["sent_s"])
        with self.assertRaises(HTTPError) as error:
            urlopen(url + "/../../pyproject.toml")
        self.assertEqual(error.exception.code, 404)
        request = Request(
            url + "/api/launch",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "https://unrelated.example"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_optional_stage_fields_remain_unmeasured(self):
        self.assertEqual(compact_event(event(0, 0, 3200, 0.3, "전사"))["stages"], {})

    def test_start_gate_holds_input_before_clock_and_preserves_complete_frames(self):
        class Adapter:
            def start_stream(self):
                return self

            def accept_chunk(self, stream, samples):
                return "음성"

            def finish_stream(self, stream):
                return "음성"

        called = []
        directory = self.root / "stream"
        telemetry = {}

        def gate():
            self.assertNotIn("t0_monotonic_ns", telemetry)
            time.sleep(0.1)
            self.assertEqual((directory / "producer.jsonl").read_text(), "")
            called.append(time.perf_counter_ns())

        def started(t0):
            self.assertEqual(t0, telemetry["t0_monotonic_ns"])
            self.assertGreaterEqual(t0, called[0])

        run_paced(
            Adapter(),
            Audio(array("f", [0.2] * 3200), 16000, "fixture"),
            {"chunk_ms": 100, "queue_capacity_chunks": 10, "drain_timeout_s": 2},
            directory,
            telemetry,
            start_gate=gate,
            on_start=started,
        )
        self.assertTrue(telemetry["complete"])
        self.assertEqual(telemetry["processed_chunks"], 2)


if __name__ == "__main__":
    unittest.main()
