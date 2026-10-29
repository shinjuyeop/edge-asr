"""통제 실행 계획·설정 불변·선택 정책. 실제 ARM64 추론은 이 테스트에 포함하지 않는다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
from edge_asr.tools.probes import comparison as helper


class ComparisonTests(unittest.TestCase):
    def test_matrix_balanced_and_interleaved(self):
        plan = helper.plan_matrix()
        self.assertEqual(len(plan), 18)
        for family in helper.FAMILIES:
            for threads in (1, 2, 4):
                self.assertEqual(
                    [
                        p["repetition"]
                        for p in plan
                        if p["family"] == family and p["num_threads"] == threads
                    ],
                    [1, 2, 3],
                )
        self.assertNotEqual(plan[:6], plan[6:12])

    def test_derived_config_only_changes_threads_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            for family in helper.FAMILIES:
                before = (ROOT / f"configs/{family}_eval.toml").read_bytes()
                target = Path(directory) / f"{family}.toml"
                helper.configuration(ROOT, family, 4, target)
                self.assertEqual(helper.load_evaluation_config(target)["model"]["num_threads"], 4)
                self.assertEqual((ROOT / f"configs/{family}_eval.toml").read_bytes(), before)
                with self.assertRaises(FileExistsError):
                    helper.configuration(ROOT, family, 1, target)

    def fixture(self, root):
        helper.save(root / "plan.json", {"phase": "matrix", "runs": helper.plan_matrix()})
        for i, item in enumerate(helper.plan_matrix(), 1):
            d = root / f"{i:02d}-{item['family']}-t{item['num_threads']}"
            run = d / "evaluations/eval-fixture"
            run.mkdir(parents=True)
            helper.save(
                d / "execution.json",
                {"status": "completed", "diagnostic_only": False, "limit_verified": True},
            )
            helper.save(
                run / "summary.json",
                {
                    "overall": {
                        "complete": True,
                        "rtf": {1: 0.103, 2: 0.1, 4: 0.12}[item["num_threads"]],
                    }
                },
            )
            helper.save(run / "manifest.json", {"warmup": {"successful": 3}})
            (run / "predictions.jsonl").write_text(
                json.dumps(
                    {
                        "id": "fixture",
                        "reference": "정답",
                        "hypothesis": "정답",
                        "audio": {"sha256": "a" * 64},
                    }
                )
                + "\n"
            )

    def test_selection_prefers_fewer_threads_within_predeclared_tolerance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            value = helper.freeze_selection(root, root / "selection.json")
            self.assertEqual(value["selected"], {"zipformer": 1, "sensevoice": 1})
            with self.assertRaises(ValueError):
                helper.freeze_selection(root, root / "selection.json")

    def test_selection_rejects_failed_runs_and_changed_hypotheses(self):
        for kind in ("failed", "hypothesis", "input"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                d = root / "18-zipformer-t1"
                if kind == "failed":
                    helper.save(d / "execution.json", {"status": "timeout"})
                else:
                    p = d / "evaluations/eval-fixture/predictions.jsonl"
                    row = json.loads(p.read_text())
                    row["reference" if kind == "input" else "hypothesis"] = "변경"
                    p.write_text(json.dumps(row) + "\n")
                with self.assertRaises(ValueError):
                    helper.freeze_selection(root, root / "selection.json")

    def test_temperature_failure_is_recorded_without_starting_inference(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(helper, "cool_down", side_effect=RuntimeError("온도 불가")),
            patch.object(helper, "device", return_value={}),
            patch.object(helper.subprocess, "Popen") as popen,
        ):
            output = Path(directory) / "run"
            with self.assertRaises(RuntimeError):
                helper.execute(
                    ROOT, output, {"family": "zipformer", "num_threads": 1}, ROOT / "missing.jsonl"
                )
            popen.assert_not_called()
            self.assertEqual(
                json.loads((output / "execution.json").read_text())["status"], "controller_failed"
            )

    def test_external_failure_and_missing_measurement_are_not_success(self):
        for code, status in (
            (124, "timeout"),
            (137, "killed_cause_unverified"),
            (1, "failed"),
            (0, "measurement_incomplete"),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                process = Mock()
                process.poll.return_value = code
                process.wait.return_value = code
                with (
                    patch.object(helper, "cool_down", return_value=0),
                    patch.object(helper, "device", return_value={}),
                    patch.object(helper.subprocess, "Popen", return_value=process),
                ):
                    output = Path(directory) / "run"
                    result = helper.execute(
                        ROOT,
                        output,
                        {"family": "zipformer", "num_threads": 1},
                        ROOT / "missing.jsonl",
                    )
                    self.assertEqual(result["status"], status)
                    self.assertFalse(result["limit_verified"])
                    self.assertEqual(
                        json.loads((output / "execution.json").read_text())["exit_code"], code
                    )

    def test_cli_preserves_later_runs_and_reports_any_child_failure(self):
        for status, expected in (("timeout", 1), ("completed", 0)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "manifest.jsonl"
                manifest.write_text("fixture\n")
                argv = [
                    "compare_models.py",
                    "diagnostics",
                    "--root",
                    str(root),
                    "--manifest",
                    str(manifest),
                    "--output",
                    str(root / "output"),
                ]

                def git(command, **kwargs):
                    return b"" if command[1] == "status" else "fixture-commit\n"

                results = [{"status": status}] + [{"status": "completed"}] * 5
                with (
                    patch.object(helper.sys, "argv", argv),
                    patch.object(helper.subprocess, "check_output", side_effect=git),
                    patch.object(helper, "execute", side_effect=results) as execute,
                ):
                    self.assertEqual(helper.main(), expected)
                    self.assertEqual(execute.call_count, 6)


if __name__ == "__main__":
    unittest.main()
