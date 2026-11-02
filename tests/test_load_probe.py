"""연속 부하 프로브의 모델 재사용·워밍업 분리·열 표본과의 시계 연결을 검사한다."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from edge_asr.tools.probes import load


class LoadProbeTests(unittest.TestCase):
    def test_one_load_three_warmups_and_monotonic_call_timestamps(self):
        adapter = Mock(model_files={})
        adapter.transcribe.return_value = "안녕"
        config = {"evaluation": {"mode": "batch"}, "audio": {"sample_rate_hz": 16000}, "model": {}}
        source = SimpleNamespace(id="one", audio_path=Path("one.wav"), audio_sha256="wav")
        audio = SimpleNamespace(sha256="wav", duration_s=3.0)
        sampler = Mock()
        sampler.__enter__ = Mock(return_value=sampler)
        sampler.__exit__ = Mock(return_value=False)
        sampler.metadata.return_value = {"rss_observed_max_mib": 1}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(load, "load_evaluation_config", return_value=config),
                patch.object(load, "load_manifest", return_value=([source], "manifest")),
                patch.object(load, "load_audio", return_value=audio),
                patch.object(load, "digest", return_value="config"),
                patch.object(load, "create_adapter", return_value=adapter),
                patch.object(load, "RssSampler", return_value=sampler),
                patch.object(load, "environment", return_value={}),
                patch.object(load.subprocess, "check_output", return_value="fixture"),
                patch.object(load.time, "perf_counter", side_effect=[0, 1, 2, 3, 4, 5, 182, 183]),
                patch.object(load.time, "monotonic", side_effect=[10, 11, 12]),
            ):
                load.probe(root, root / "config", root / "manifest", root, 180)
            worker = json.loads((root / "worker.json").read_text())
            call = json.loads((root / "calls.jsonl").read_text())
            self.assertEqual(worker["status"], "completed")
            self.assertEqual(worker["completed_calls"], 1)
            self.assertEqual(worker["inference_started_monotonic_s"], 10)
            self.assertEqual(call["started_monotonic_s"], 11)
            self.assertEqual(call["ended_monotonic_s"], 12)
            self.assertEqual(call["inference_s"], 1)
            self.assertEqual(call["cycle"], 0)
            adapter.load.assert_called_once()
            self.assertEqual(adapter.transcribe.call_count, 4)
            adapter.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
