"""실제 마이크 없이 PCM 보존·종료·유한 큐·장치 실패를 검증한다."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from edge_asr.dataset import load_audio
from edge_asr.microphone import devices, record


class MicrophoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.telemetry, self.frames = {}, []

    def accept(self, pcm, start, end):
        self.assertEqual(start, sum(len(p) for p in self.frames))
        self.assertEqual(end - start, len(pcm))
        self.frames.append(pcm)

    def run_capture(self, source, *, seconds=0.2, accept=None, stop_signal=None):
        return record(
            "fixture",
            seconds,
            self.root,
            self.telemetry,
            accept or self.accept,
            stop_signal=stop_signal,
            command=[sys.executable, "-c", source],
        )

    def test_device_ids_are_stable_card_names_and_arguments_are_not_shell(self):
        result = subprocess.CompletedProcess(
            [], 0, "card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]\n", ""
        )
        with patch("edge_asr.microphone.subprocess.run", return_value=result) as run:
            self.assertEqual(devices()[0]["id"], "plughw:CARD=Device,DEV=0")
        self.assertEqual(run.call_args.args[0], ["arecord", "-l"])

    def test_finite_pcm_and_wave_are_identical_and_never_overwritten(self):
        audio = self.run_capture(
            "import sys,time; sys.stdout.buffer.write(b'\\xd2\\x04'*3200); sys.stdout.buffer.flush(); time.sleep(2)"
        )
        self.assertTrue(self.telemetry["complete"])
        self.assertEqual(len(audio.samples), 3200)
        self.assertTrue(all(x == 1234 / 32768 for p in self.frames for x in p))
        self.assertEqual(self.telemetry["dropped_chunks"], 0)
        with self.assertRaises(FileExistsError):
            self.run_capture("")

    def test_requested_finish_drains_all_accepted_pcm(self):
        stop = self.root / "finish.json"

        def consume(pcm, start, end):
            self.accept(pcm, start, end)
            stop.write_text("{}")

        audio = self.run_capture(
            "import sys,time\nwhile True:\n sys.stdout.buffer.write(b'\\x00\\x10'*1600);sys.stdout.buffer.flush();time.sleep(.03)",
            seconds=2,
            accept=consume,
            stop_signal=stop,
        )
        self.assertEqual(self.telemetry["stop_reason"], "requested")
        self.assertTrue(self.telemetry["complete"])
        self.assertEqual(len(audio.samples), sum(len(p) for p in self.frames))

    def test_device_disconnect_is_a_failure_with_recording_preserved(self):
        with self.assertRaisesRegex(RuntimeError, "장치가 입력 도중 종료"):
            self.run_capture("import sys;sys.stdout.buffer.write(b'\\x00\\x10'*1600)", seconds=2)
        self.assertFalse(self.telemetry["complete"])
        self.assertEqual(len(load_audio(self.root / "recording.wav", 16000).samples), 1600)

    def test_full_queue_does_not_silently_join_audio_across_a_gap(self):
        def slow(pcm, start, end):
            self.accept(pcm, start, end)
            time.sleep(0.03)

        with self.assertRaisesRegex(RuntimeError, "capture_gap"):
            self.run_capture(
                "import sys,time;sys.stdout.buffer.write(b'\\x00\\x10'*160000);sys.stdout.buffer.flush();time.sleep(2)",
                seconds=10,
                accept=slow,
            )
        self.assertFalse(self.telemetry["complete"])
        self.assertEqual(self.telemetry["dropped_chunks"], 1)
        rows = [
            json.loads(s) for s in (self.root / "stream/producer.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[-1]["type"], "drop")

    def test_hardware_overrun_is_not_marked_complete(self):
        with self.assertRaisesRegex(RuntimeError, "하드웨어 입력 넘침"):
            self.run_capture(
                "import sys,time;sys.stderr.write('overrun!!!\\n');sys.stderr.flush();sys.stdout.buffer.write(b'\\x00\\x10'*3200);sys.stdout.buffer.flush();time.sleep(2)"
            )
        self.assertEqual(self.telemetry["hardware_overruns"], 1)
        self.assertFalse(self.telemetry["complete"])
