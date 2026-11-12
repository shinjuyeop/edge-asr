"""설치된 CLI의 출력 계약과 실패 시 종료 코드를 확인한다."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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

    def test_evaluate_requires_an_explicit_model_config(self):
        result = self.run_cli("evaluate", "--manifest", "missing.jsonl")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--config", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_tools_lists_current_and_legacy_commands_separately(self):
        current = self.run_cli("tools", "list")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertIn("replay-qwen3", current.stdout)
        self.assertNotIn("repeat-zipformer", current.stdout)
        legacy = self.run_cli("tools", "list", "--group", "legacy")
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertIn("repeat-zipformer", legacy.stdout)
        self.assertNotIn("replay-qwen3", legacy.stdout)
        all_tools = self.run_cli("tools", "list", "--group", "all")
        self.assertEqual(all_tools.returncode, 0, all_tools.stderr)
        for command in ("replay-qwen3", "repeat-zipformer", "consolidate", "opt0-baseline"):
            self.assertIn(command, all_tools.stdout)

    def test_old_tool_alias_still_dispatches_without_loading_a_model(self):
        result = self.run_cli("tools", "prepare_m30_audio", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
