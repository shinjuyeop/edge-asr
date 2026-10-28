"""임시 Git 저장소에서 통합·자료 보존·거부·실패 복원을 검증한다. RP5 사용 없음."""

import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "consolidate_rp5", Path(__file__).resolve().parents[1] / "scripts/consolidate_rp5.py"
)
consolidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consolidate)


def command(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


@unittest.skipUnless(shutil.which("git") and platform.system() == "Linux", "Linux/Git CLI 필요")
class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / "edge-asr-git", self.root / "edge-asr"
        self.legacy = self.root / "edge-asr-bench-20261028-01"
        for folder in (self.source, self.target, self.legacy):
            folder.mkdir()
        command(self.source, "init", "-b", "main")
        command(self.source, "config", "user.name", "Local Test")
        command(self.source, "config", "user.email", "local-test@example.invalid")
        (self.source / ".gitignore").write_text("/.local-backups/\n/.venv/\n/models/*\n!/models/README.md\n/benchmarks/runs/\n")
        (self.source / "README.md").write_text("current source\n")
        (self.source / "models").mkdir()
        (self.source / "models/README.md").write_text("local weights only\n")
        (self.source / "scripts").mkdir()
        (self.source / "scripts/example.py").write_text("print('source')\n")
        command(self.source, "add", ".")
        command(self.source, "commit", "-m", "fixture")
        command(self.source, "remote", "add", "origin", "https://github.com/shinjuyeop/edge-asr.git")
        self.commit = command(self.source, "rev-parse", "HEAD")
        command(self.source, "update-ref", "refs/remotes/origin/main", self.commit)
        (self.target / "README.md").write_text("original RP5 file\n")
        (self.target / ".venv/bin").mkdir(parents=True)
        (self.target / ".venv/bin/python").write_bytes(b"original interpreter fixture")
        (self.target / "models").mkdir()
        (self.target / "models/model.onnx").write_bytes(b"original weights fixture")
        (self.legacy / "benchmarks/runs/run-001").mkdir(parents=True)
        (self.legacy / "benchmarks/runs/run-001/summary.md").write_text("original successful measurements\n")

    def prepare(self):
        return consolidate.plan(self.source, self.target, [self.legacy])

    def assert_assets_untouched(self):
        self.assertEqual((self.target / ".venv/bin/python").read_bytes(), b"original interpreter fixture")
        self.assertEqual((self.target / "models/model.onnx").read_bytes(), b"original weights fixture")

    def test_preview_does_not_write_or_move(self):
        prepared = self.prepare()
        self.assertIn("README.md", prepared["backup_files"])
        self.assertFalse((self.target / ".local-backups").exists())
        self.assertTrue(self.source.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual((self.target / "README.md").read_text(), "original RP5 file\n")
        self.assert_assets_untouched()

    def test_apply_keeps_environment_and_archives_both_folders(self):
        receipt_path = consolidate.apply_plan(self.prepare())
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["status"], "completed")
        self.assertFalse(self.source.exists())
        self.assertFalse(self.legacy.exists())
        self.assertEqual(command(self.target, "rev-parse", "HEAD"), self.commit)
        self.assertEqual(command(self.target, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual((self.target / "README.md").read_text(), "current source\n")
        self.assertEqual((receipt_path.parent / "original-files/README.md").read_text(), "original RP5 file\n")
        archived = Path(receipt["archive_mapping"][str(self.legacy)])
        self.assertEqual((archived / "benchmarks/runs/run-001/summary.md").read_text(), "original successful measurements\n")
        self.assertEqual(command(self.target, "check-ignore", str(receipt_path.relative_to(self.target))), str(receipt_path.relative_to(self.target)))
        self.assert_assets_untouched()

    def test_cli_can_archive_its_own_source_working_directory(self):
        helper = self.source / "scripts/consolidate_rp5.py"
        shutil.copy2(SPEC.origin, helper)
        command(self.source, "add", "scripts/consolidate_rp5.py")
        command(self.source, "commit", "-m", "helper fixture")
        revision = command(self.source, "rev-parse", "HEAD")
        command(self.source, "update-ref", "refs/remotes/origin/main", revision)
        result = subprocess.run([
            sys.executable, str(helper), "--source", str(self.source), "--target", str(self.target),
            "--legacy-dir", str(self.legacy), "--apply",
        ], cwd=self.source, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.source.exists())
        self.assertEqual(command(self.target, "rev-parse", "HEAD"), revision)
        self.assert_assets_untouched()

    def test_failure_restores_original_files_and_directories(self):
        prepared = self.prepare()
        rename = Path.rename

        def failing_rename(path, destination):
            if path == self.legacy:
                raise OSError("synthetic archive failure")
            return rename(path, destination)

        with patch.object(Path, "rename", failing_rename), self.assertRaises(OSError):
            consolidate.apply_plan(prepared)
        self.assertTrue(self.source.is_dir())
        self.assertTrue(self.legacy.is_dir())
        self.assertFalse((self.target / ".git").exists())
        self.assertFalse((self.target / "scripts/example.py").exists())
        self.assertEqual((self.target / "README.md").read_text(), "original RP5 file\n")
        receipt = next((self.target / ".local-backups").glob("*/receipt.json"))
        self.assertEqual(json.loads(receipt.read_text())["status"], "rolled_back")
        self.assert_assets_untouched()

    def test_modified_source_rejected(self):
        (self.source / "README.md").write_text("uncommitted work\n")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.target / ".local-backups").exists())

    def test_interruption_after_source_archive_is_recoverable(self):
        prepared = self.prepare()
        rename = Path.rename

        def interrupted_rename(path, destination):
            result = rename(path, destination)
            if path == self.source:
                raise KeyboardInterrupt
            return result

        with patch.object(Path, "rename", interrupted_rename), self.assertRaises(KeyboardInterrupt):
            consolidate.apply_plan(prepared)
        self.assertTrue(self.source.is_dir())
        self.assertTrue(self.legacy.is_dir())
        self.assertFalse((self.target / ".git").exists())
        self.assertEqual((self.target / "README.md").read_text(), "original RP5 file\n")
        self.assert_assets_untouched()

    def test_active_benchmark_rejected(self):
        with patch.object(consolidate, "active_benchmarks", return_value=[12345]), self.assertRaises(ValueError):
            self.prepare()

    def test_symlink_destination_rejected(self):
        external = self.root / "external.md"
        external.write_text("keep outside content\n")
        (self.target / "README.md").unlink()
        (self.target / "README.md").symlink_to(external)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(external.read_text(), "keep outside content\n")

    def test_hardlinked_destination_does_not_modify_other_link(self):
        external = self.root / "external.md"
        external.hardlink_to(self.target / "README.md")
        consolidate.apply_plan(self.prepare())
        self.assertEqual(external.read_text(), "original RP5 file\n")
        self.assertEqual((self.target / "README.md").read_text(), "current source\n")

    def test_existing_related_git_is_backed_up(self):
        shutil.copytree(self.source / ".git", self.target / ".git")
        receipt_path = consolidate.apply_plan(self.prepare())
        self.assertTrue((receipt_path.parent / "original.git/HEAD").is_file())
        self.assertEqual(command(self.target, "rev-parse", "HEAD"), self.commit)

    def test_interrupt_after_git_install_restores_original_git(self):
        shutil.copytree(self.source / ".git", self.target / ".git")
        prepared = self.prepare()
        rename = Path.rename

        def interrupted_rename(path, destination):
            result = rename(path, destination)
            if path.name == "incoming.git":
                raise KeyboardInterrupt
            return result

        with patch.object(Path, "rename", interrupted_rename), self.assertRaises(KeyboardInterrupt):
            consolidate.apply_plan(prepared)
        self.assertEqual(command(self.target, "rev-parse", "HEAD"), self.commit)
        self.assertEqual((self.target / "README.md").read_text(), "original RP5 file\n")
        self.assertTrue(self.source.is_dir())
        self.assertTrue(self.legacy.is_dir())
        self.assert_assets_untouched()

    def test_tracked_weights_rejected(self):
        (self.source / "models/model.onnx").write_bytes(b"must not replace RP5 weights")
        command(self.source, "add", "--force", "models/model.onnx")
        command(self.source, "commit", "-m", "invalid asset fixture")
        revision = command(self.source, "rev-parse", "HEAD")
        command(self.source, "update-ref", "refs/remotes/origin/main", revision)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assert_assets_untouched()

    def test_existing_unrelated_git_rejected(self):
        command(self.target, "init", "-b", "main")
        command(self.target, "config", "user.name", "Local Test")
        command(self.target, "config", "user.email", "local-test@example.invalid")
        command(self.target, "add", "README.md")
        command(self.target, "commit", "-m", "other history")
        old_head = command(self.target, "rev-parse", "HEAD")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(command(self.target, "rev-parse", "HEAD"), old_head)

    def test_separate_source_environment_rejected(self):
        (self.source / ".venv").mkdir()
        with self.assertRaises(ValueError):
            self.prepare()
