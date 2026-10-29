"""통합 명령·선택 런타임 분리·원자적 산출물·문서 경로 회귀 검사."""

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edge_asr.artifacts import digest, write_json
from edge_asr.tools.cli import COMMANDS, main

ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTests(unittest.TestCase):
    def test_each_tool_help_from_another_working_directory_without_models(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        with tempfile.TemporaryDirectory() as directory:
            for command in COMMANDS:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, "-m", "edge_asr", "tools", command, "--help"],
                        cwd=directory,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)

    def test_importing_tools_does_not_load_native_runtimes_or_evaluation(self):
        code = """
from importlib import import_module
from edge_asr.tools.cli import COMMANDS, module_name
import sys
for command in COMMANDS:
    import_module(module_name(command))
assert 'numpy' not in sys.modules
assert 'sherpa_onnx' not in sys.modules
assert 'edge_asr.evaluation' not in sys.modules
"""
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dispatch_restores_arguments_on_error(self):
        original = sys.argv
        with patch("edge_asr.tools.cli.import_module", side_effect=ValueError("fixture")):
            with self.assertRaises(ValueError):
                main(["prepare-kss", "--help"])
        self.assertIs(sys.argv, original)

    def test_atomic_json_failure_preserves_existing_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            write_json(path, {"status": "running", "text": "한글"})
            before = digest(path)
            with self.assertRaises(ValueError):
                write_json(path, {"value": float("nan")})
            self.assertEqual(digest(path), before)
            with patch.object(Path, "replace", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    write_json(path, {"status": "complete"})
            self.assertEqual(digest(path), before)
            self.assertEqual(list(path.parent.iterdir()), [path])
            write_json(path, {"status": "complete"})
            self.assertEqual(json.loads(path.read_text())["status"], "complete")

    def test_dependency_direction_does_not_pull_commands_into_core_or_reports(self):
        for path in (ROOT / "src/edge_asr").rglob("*.py"):
            if path.name in ("cli.py", "__main__.py"):
                continue
            is_core = "tools" not in path.parts
            is_report = path.parent.name == "reports"
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if is_core:
                        self.assertFalse(module.startswith("edge_asr.tools"), str(path))
                    if is_report:
                        self.assertFalse(module.startswith("edge_asr.tools.probes"), str(path))

    def test_local_document_links_resolve(self):
        markdown = [ROOT / "README.md"]
        for name in ("docs", "configs", "benchmarks", "experiments", "results/summaries"):
            markdown.extend((ROOT / name).rglob("*.md"))
        for path in markdown:
            for match in re.finditer(r"\]\(([^)\n]+)\)", path.read_text()):
                target = match.group(1)
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(
                    ("#", "/", "<")
                ):
                    continue
                target = target.split("#")[0]
                if Path(target).suffix not in (".md", ".py", ".toml", ".json"):
                    continue
                self.assertTrue(
                    (path.parent / target).exists(), f"{path.relative_to(ROOT)}: {target}"
                )


if __name__ == "__main__":
    unittest.main()
