"""설치된 CLI의 출력 계약과 실패 시 종료 코드를 확인한다."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "edge_asr", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_env(self):
        result = self.run_cli("env")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("machine", json.loads(result.stdout))

    def test_check_config(self):
        result = self.run_cli("check-config", str(ROOT / "configs/legacy/zipformer_streaming.toml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"]["family"], "zipformer")

    def test_config_error(self):
        result = self.run_cli("check-config", str(ROOT / "tests/nonexistent.toml"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("오류:", result.stderr)
