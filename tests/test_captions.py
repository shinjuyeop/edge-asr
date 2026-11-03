"""실제 모델 없이 분리 표시·원문 보존·늦은 반환·터미널 실패·실제 IPC를 검증한다."""

import contextlib
import io
import json
import tempfile
import time
import unittest
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

import test_online_vad as vad_fixture

from edge_asr.captions import CaptionReplay, render
from edge_asr.streaming import read_events
from edge_asr.tools.live import start, summarize
from edge_asr.tools.probes import thermal as guard


class CaptionTests(unittest.TestCase):
    def make(self, folder, submit=None):
        z = self.enterContext((folder / "zip").open("x"))
        j = self.enterContext((folder / "jobs").open("x"))
        c = self.enterContext((folder / "captions").open("x"))
        return CaptionReplay(
            Mock(),
            [{"id": "a", "start": 0, "end": 3200}, {"id": "b", "start": 3200, "end": 6400}],
            {},
            submit or (lambda job: ("enqueue", time.perf_counter_ns())),
            z,
            j,
            vad=Mock(),
            policy="vad0",
            caption_log=c,
        )

    def test_literal_outputs_and_late_refine_stay_in_original_source(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            replay = self.make(Path(d))
            replay.offer_payload(array("f", [0] * 1600), 0, 1600, replay.sources[0])
            replay._observe("partial", None, "원래  말..", 1600, 100, 0)
            replay._observe("partial", None, "원래  말..", 3200, 101, 0)
            replay._observe("final", None, "원래  말..", 3200, 102, 0)
            replay._observe("partial", None, "다음", 4800, 103, 1)
            job = replay.jobs[0]
            replay.receive(
                {**job, "status": "success", "hypothesis": "  보정.. 보정  ", "ended_ns": 104}, 105
            )
            self.assertEqual(replay.events[-1]["source_id"], "a")
            self.assertEqual(replay.events[-1]["text"], "  보정.. 보정  ")
            self.assertEqual(replay.last_partial["a"], "원래  말..")
            self.assertEqual(replay.last_partial["b"], "다음")
            self.assertEqual(sum(e["kind"] == "zip_partial" for e in replay.events), 2)
            self.assertTrue(replay.display_complete())
            with self.assertRaises(ValueError):
                replay.receive({**job, "ended_ns": 104}, 106)

    def test_hash_mismatch_failed_empty_and_dropped_results_are_explicit(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            replay = self.make(Path(d))
            replay.offer_payload(array("f", [0] * 1600), 0, 1600, replay.sources[0])
            self.assertFalse(replay.display_complete())
            job = replay.jobs[0]
            with self.assertRaises(ValueError):
                replay.receive({**job, "payload_float32_sha256": "bad"}, 1)
            replay.receive({**job, "status": "failed", "error": "native failure", "ended_ns": 1}, 2)
            self.assertEqual(replay.events[-1]["kind"], "refine_failed")
            self.assertTrue(replay.display_complete())
            replay.submit = lambda job: ("drop", time.perf_counter_ns())
            replay.offer_payload(array("f", [0] * 1600), 1600, 3200, replay.sources[0])
            self.assertEqual(replay.events[-1]["kind"], "refine_dropped")
            with self.assertRaises(ValueError):
                replay.receive(replay.jobs[1], 3)
            replay.submit = lambda job: ("enqueue", time.perf_counter_ns())
            replay.offer_payload(array("f", [0] * 1600), 3200, 4800, replay.sources[1])
            replay.receive(
                {**replay.jobs[2], "status": "success", "hypothesis": "", "ended_ns": 4}, 5
            )
            self.assertEqual(replay.events[-1]["kind"], "refine_candidate")
            self.assertEqual(replay.events[-1]["text"], "")

    def test_render_escapes_controls_without_modifying_original(self):
        event = {
            "kind": "refine_candidate",
            "source_id": "a\n",
            "text": "가\n\x1b[31m",
            "source_start_s": 0.0,
            "source_end_s": 1.0,
        }
        text = render(event)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\n", text)
        self.assertIn("자동 적용 안 함", text)
        self.assertEqual(event["text"], "가\n\x1b[31m")

    def test_existing_output_and_invalid_source_selection_create_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(ValueError):
                start(root)
            with self.assertRaises(ValueError):
                start(root, wav=root / "a", manifest=root / "b")
            with self.assertRaises(FileExistsError):
                start(root, wav=root / "a", output=root)
            self.assertEqual(list(root.iterdir()), [])

    def test_guard_console_callback_preserves_log_and_fails_on_display_error(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                child = Mock()
                child.poll.return_value = 0
                child.wait.return_value = 0
                child.stdout = io.StringIO("one\ntwo\n")
                lines = []

                def display(line):
                    lines.append(line)
                    if fail:
                        raise BrokenPipeError("fixture closed terminal")

                cool = {
                    "temperature_mc": 52000,
                    "cpu0_freq_khz": 2400000,
                    "current_limits": 0,
                    "history_limits": 0,
                }
                with (
                    patch.object(guard.subprocess, "check_output", return_value="mock"),
                    patch.object(guard, "admit", return_value=cool),
                    patch.object(guard, "sample", return_value=cool),
                    patch.object(guard.subprocess, "Popen", return_value=child),
                    patch.object(guard, "terminate"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = guard.run(root, root / "run", ["mock"], console_callback=display)
                self.assertEqual(code, 1)  # short fixture has no verified worker PID
                receipt = json.loads((root / "run/execution.json").read_text())
                if fail:
                    self.assertIn("console_forward_failed", receipt["error"])
                else:
                    self.assertEqual(lines, ["one\n", "two\n"])
                self.assertEqual((root / "run/console.log").read_text(), "".join(lines))


class CaptionIpcTests(unittest.TestCase):
    def setUp(self):
        self.fixture = vad_fixture.OnlineVadCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def run_display(self, silent=False):
        if silent:
            self.fixture.fixture.env["FIXTURE_VAD_SILENT"] = "1"
        result, native = self.fixture.fixture.run_fixture(
            2,
            [
                "--refine-policy",
                "vad0",
                "--vad-model",
                str(self.fixture.vad_path),
                "--caption-display",
            ],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / "run").rename(self.root / "runtime")
        events = read_events(self.root / "runtime/caption-events.jsonl")
        shown = [
            {
                "event": e,
                "stdout_started_ns": e["emitted_ns"] + 1,
                "stdout_flushed_ns": e["emitted_ns"] + 2,
            }
            for e in events
        ]
        (self.root / "terminal-events.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in shown)
        )
        return events, native

    def test_mock_models_real_ipc_returns_candidates_preserves_zip_and_rejects_tampering(self):
        events, native = self.run_display()
        report = summarize(self.root, True)
        self.assertEqual(report["automatic_replacements"], 0)
        self.assertEqual(report["zip_cer"], native["zip_cer"])
        self.assertEqual(report["zip_final_displays"], 2)
        self.assertEqual(report["refine_candidate_displays"], 2)
        self.assertTrue(all(e["text"] == "가" for e in events if e["kind"] == "zip_final"))
        self.assertIsNone(summarize(self.root, False)["zip_cer"])
        path = self.root / "terminal-events.jsonl"
        rows = read_events(path)
        rows.pop()
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(ValueError):
            summarize(self.root, True)

    def test_silence_never_calls_refine_but_zip_final_remains_available(self):
        self.run_display(silent=True)
        report = summarize(self.root, False)
        self.assertEqual(report["refine_candidate_displays"], 0)
        self.assertEqual(report["zip_final_displays"], 2)
        self.assertEqual(report["refine_owned_end_to_stdout_s"]["n"], 0)
        self.assertIsNone(report["refine_owned_end_to_stdout_s"]["mean"])
