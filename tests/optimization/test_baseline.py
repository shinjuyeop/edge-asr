"""실제 모델 없이 반복 순서·단계 분리·실패·미주석 CER 보존을 검사한다."""

import json
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from edge_asr.artifacts import digest, write_jsonl
from edge_asr.dataset import write_wav
from edge_asr.tools.optimization import baseline

ROOT = Path(__file__).resolve().parents[2]


class Adapter:
    model_files = {}
    metadata = {"fixture": True}

    def __init__(self, *, fail_load=False, fail_second=False):
        self.fail_load, self.fail_second = fail_load, fail_second
        self.calls, self.closed = [], False
        self.last_result_metadata = None

    def load(self):
        if self.fail_load:
            raise RuntimeError("fixture allocation failed")

    def transcribe(self, audio):
        value = round(audio.samples[0] * 32768)
        self.calls.append(value)
        self.last_result_metadata = {"native_stderr": "", "tokens": ["가"]}
        if self.fail_second and value == 2:
            self.last_result_metadata["native_stderr"] = "fixture repetition loop"
            raise RuntimeError("fixture native warning")
        return "가"

    def close(self):
        self.closed = True


class BaselineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = ROOT / "configs/optimization/opt0_onnx_2t.toml"
        self.manifest = self.root / "input.jsonl"
        self.output = self.root / "run"
        self.rows = []
        for n in (1, 2):
            path = self.root / f"{n}.wav"
            write_wav(path, array("h", [n] * 1600))
            self.rows.append(
                {
                    "id": str(n),
                    "audio_path": str(path),
                    "reference": "가",
                    "audio_sha256": digest(path),
                }
            )
        write_jsonl(self.manifest, self.rows)

    def execute(self, adapter, *, protocol="baseline", repetitions=2, order_offset=0):
        with (
            patch.object(baseline, "limits_verified", return_value=True),
            patch.object(baseline, "environment", return_value={"fixture": True}),
            patch.object(baseline.subprocess, "check_output", side_effect=["fixture", b""]),
        ):
            code = baseline.run(
                self.root,
                self.config,
                self.manifest,
                self.output,
                protocol=protocol,
                repetitions=repetitions,
                order_offset=order_offset,
                adapter_factory=lambda *args: adapter,
            )
        return code, json.loads((self.output / "worker.json").read_text())

    def test_missing_external_limit_rejects_before_creating_worker(self):
        with patch.object(baseline, "limits_verified", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "4GiB"):
                baseline.run(self.root, self.config, self.manifest, self.output)
        self.assertFalse(self.output.exists())

    def test_first_warmup_and_rotated_rounds_use_one_model_and_common_scores(self):
        adapter = Adapter()
        code, worker = self.execute(adapter)
        self.assertEqual(code, 0)
        self.assertEqual(adapter.calls, [1, 1, 1, 1, 1, 2, 2, 1])
        self.assertTrue(adapter.closed)
        phases = [row["phase"] for row in worker["calls"]]
        self.assertEqual(phases, ["first_inference", "warmup", "warmup", "warmup"] + ["steady"] * 4)
        summary = json.loads((self.output / "summary.json").read_text())["overall"]
        self.assertEqual(
            (summary["planned"], summary["successful"], summary["cer"]["value"]), (4, 4, 0)
        )
        self.assertIsNone(worker["unmeasured"]["ttft_s"])
        before = (self.output / "worker.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.execute(Adapter())
        self.assertEqual((self.output / "worker.json").read_bytes(), before)

    def test_independent_run_rotates_only_scored_inputs_and_keeps_warmup_fixed(self):
        adapter = Adapter()
        code, _ = self.execute(adapter, repetitions=1, order_offset=1)
        self.assertEqual(code, 0)
        self.assertEqual(adapter.calls, [1, 1, 1, 1, 2, 1])

    def test_failure_keeps_native_metadata_and_does_not_shrink_denominator(self):
        code, worker = self.execute(Adapter(fail_second=True))
        self.assertEqual((code, worker["status"]), (1, "completed_with_model_failures"))
        rows = [
            json.loads(line)
            for line in (self.output / "predictions.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["adapter_result"]["native_stderr"], "fixture repetition loop")
        summary = json.loads((self.output / "summary.json").read_text())["overall"]
        self.assertEqual((summary["failed"], summary["reference_character_coverage"]), (2, 0.5))
        self.assertFalse(summary["complete"])

    def test_diagnostic_completion_is_distinct_from_model_success_and_has_no_cer(self):
        for row in self.rows:
            row["reference"] = ""
        write_jsonl(self.manifest, self.rows)
        code, worker = self.execute(
            Adapter(fail_second=True), protocol="reproduction", repetitions=1
        )
        self.assertEqual((code, worker["status"]), (0, "completed_with_model_failures"))
        self.assertEqual([r["phase"] for r in worker["calls"]], ["diagnostic"] * 2)
        summary = json.loads((self.output / "summary.json").read_text())["overall"]
        self.assertIsNone(summary["cer"]["value"])
        self.assertEqual(summary["failed"], 1)

    def test_load_failure_preserves_unattempted_rows_and_load_observations(self):
        adapter = Adapter(fail_load=True)
        code, worker = self.execute(adapter)
        self.assertEqual((code, worker["status"]), (1, "failed"))
        self.assertIsNotNone(worker["model_load"]["resources"])
        self.assertEqual(adapter.calls, [])
        self.assertTrue(adapter.closed)
        self.assertEqual(
            json.loads((self.output / "summary.json").read_text())["overall"]["not_attempted"], 4
        )

    def test_hash_mismatch_rejects_before_loading_model(self):
        self.rows[0]["audio_sha256"] = "0" * 64
        write_jsonl(self.manifest, self.rows)
        adapter = Adapter()
        code, worker = self.execute(adapter)
        self.assertEqual((code, worker["status"]), (1, "failed"))
        self.assertIn("SHA-256", worker["error"])
        self.assertEqual(adapter.calls, [])
        self.assertIsNone(worker["model_load"])

    def test_interrupted_call_keeps_its_identity_and_is_not_marked_unattempted(self):
        adapter = Adapter()
        original = adapter.transcribe

        def interrupt(audio):
            if round(audio.samples[0] * 32768) == 2:
                raise KeyboardInterrupt()
            return original(audio)

        adapter.transcribe = interrupt
        for row in self.rows:
            row["reference"] = ""
        write_jsonl(self.manifest, self.rows)
        with self.assertRaises(KeyboardInterrupt):
            self.execute(adapter, protocol="reproduction", repetitions=1)
        worker = json.loads((self.output / "worker.json").read_text())
        self.assertEqual(worker["active_call"]["source_id"], "2")
        rows = [
            json.loads(line)
            for line in (self.output / "predictions.jsonl").read_text().splitlines()
        ]
        self.assertEqual([row["status"] for row in rows], ["success", "interrupted"])
        self.assertTrue(adapter.closed)


if __name__ == "__main__":
    unittest.main()
