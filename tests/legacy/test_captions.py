"""실제 모델 없이 분리 표시·원문 보존·늦은 반환·터미널 실패·실제 IPC를 검증한다."""

import contextlib
import hashlib
import io
import json
import tempfile
import time
import unittest
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr.legacy.realtime.captions import CaptionReplay, render
from edge_asr.legacy.tools import live
from edge_asr.legacy.tools.live import start
from edge_asr.legacy.tools.reports.captions import summarize
from edge_asr.streaming import read_events
from edge_asr.tools.probes import thermal as guard
from tests.legacy import test_online_vad as vad_fixture


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
            events = read_events(Path(replay.caption_log.name))
            self.assertEqual(events[-1]["source_id"], "a")
            self.assertEqual(events[-1]["text"], "  보정.. 보정  ")
            self.assertEqual([e["event_index"] for e in events], list(range(replay.event_count)))
            self.assertEqual(replay.last_partial["a"], "원래  말..")
            self.assertEqual(replay.last_partial["b"], "다음")
            self.assertEqual(sum(e["kind"] == "zip_partial" for e in events), 2)
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
            self.assertEqual(
                read_events(Path(replay.caption_log.name))[-1]["kind"], "refine_failed"
            )
            self.assertTrue(replay.display_complete())
            replay.submit = lambda job: ("drop", time.perf_counter_ns())
            replay.offer_payload(array("f", [0] * 1600), 1600, 3200, replay.sources[0])
            self.assertEqual(
                read_events(Path(replay.caption_log.name))[-1]["kind"], "refine_dropped"
            )
            with self.assertRaises(ValueError):
                replay.receive(replay.jobs[1], 3)
            replay.submit = lambda job: ("enqueue", time.perf_counter_ns())
            replay.offer_payload(array("f", [0] * 1600), 3200, 4800, replay.sources[1])
            replay.receive(
                {**replay.jobs[2], "status": "success", "hypothesis": "", "ended_ns": 4}, 5
            )
            last = read_events(Path(replay.caption_log.name))[-1]
            self.assertEqual(last["kind"], "refine_candidate")
            self.assertEqual(last["text"], "")

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
            with self.assertRaises(ValueError):
                start(root, wav=root / "a", refine_model="unknown")
            self.assertEqual(list(root.iterdir()), [])

    def test_model_selection_requires_only_selected_assets_and_reaches_guard(self):
        from edge_asr.adapters import create_adapter
        from edge_asr.artifacts import digest
        from edge_asr.config import load_evaluation_config
        from edge_asr.qwen.onnx import MODEL_FILES

        repository = Path(__file__).resolve().parents[2]
        for model in ("sensevoice", "qwen3"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "configs/legacy").mkdir(parents=True)
                for filename in ("zipformer_paced.toml", live.REFINE_CONFIGS[model]):
                    config_path = root / "configs/legacy" / filename
                    config_path.write_bytes((repository / "configs/legacy" / filename).read_bytes())
                    config = load_evaluation_config(config_path)
                    if config["model"]["family"] == "qwen3_asr":
                        paths = [root / config["model"]["path"] / name for name in MODEL_FILES]
                    else:
                        paths = create_adapter(config["model"], 16000, root).model_files.values()
                    for path in paths:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"fixture model; never loaded")
                vad = root / "models/silero-vad-k2-20261029/silero_vad.onnx"
                vad.parent.mkdir(parents=True)
                vad.write_bytes(b"fixture vad; never loaded")
                wav = root / "voice.wav"
                vad_fixture.fixtures.write_wav(wav, array("h", [1] * 1600))
                output = root / "output"
                with (
                    patch.object(live.subprocess, "check_output", return_value=b""),
                    patch.object(live.sys, "platform", "linux"),
                    patch("edge_asr.vad.MODEL_SHA", digest(vad)),
                    patch(
                        "edge_asr.qwen.onnx.MODEL_FILES",
                        {
                            name: hashlib.sha256(b"fixture model; never loaded").hexdigest()
                            for name in MODEL_FILES
                        },
                    ),
                    patch.object(guard, "run", return_value=1) as run,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    kwargs = {} if model == "sensevoice" else {"refine_model": model}
                    self.assertEqual(start(root, wav=wav, output=output, **kwargs), 1)
                command = run.call_args.args[2]
                self.assertIn("--caption-display", command)
                self.assertEqual(command[command.index("--refine-threads") + 1], "2")
                if model == "qwen3":
                    self.assertEqual(
                        command[command.index("--refine-config") + 1],
                        str(root / "configs/legacy/qwen3_eval.toml"),
                    )
                else:
                    self.assertNotIn("--refine-config", command)
                summary = json.loads((output / "summary.json").read_text())
                self.assertEqual(summary["status"], "failed_or_unverified")
                self.assertEqual(summary["automatic_replacements"], 0)

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
