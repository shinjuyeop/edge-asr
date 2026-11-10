"""실제 모델 없이 GGUF ABI/자산·실패·UTF-8·공통 평가 계약을 확인한다."""

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

from edge_asr.adapters import create_adapter
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import Audio
from edge_asr.qwen import gguf

ROOT = Path(__file__).resolve().parents[2]


class GGUFTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = load_evaluation_config(ROOT / "configs/gguf.toml")["model"]
        self.model.update(path="model", runtime_library="runtime.so", runtime_manifest="build.json")
        directory = self.root / "model"
        directory.mkdir()
        self.expected = {}
        for name in gguf.MODEL_FILES:
            (directory / name).write_bytes(b"fixture weights")
            self.expected[name] = hashlib.sha256(b"fixture weights").hexdigest()
        (self.root / "runtime.so").write_bytes(b"fixture library")
        self.build = {
            "llama_commit": gguf.LLAMA_COMMIT,
            "library_sha256": hashlib.sha256(b"fixture library").hexdigest(),
        }
        (self.root / "build.json").write_text(json.dumps(self.build))
        assets = patch.object(gguf, "MODEL_FILES", self.expected)
        assets.start()
        self.addCleanup(assets.stop)
        self.library = Mock()
        self.library.edge_gguf_abi.return_value = 1
        self.library.edge_gguf_commit.return_value = gguf.LLAMA_COMMIT.encode()
        self.library.edge_gguf_create.return_value = 1234
        self.audio = Audio(array("f", [0.25] * 1600), 16000, "fixture")

    def load(self):
        adapter = create_adapter(self.model, 16000, self.root)
        with patch.object(ctypes, "CDLL", return_value=self.library):
            adapter.load()
        self.addCleanup(adapter.close)
        return adapter

    def result(self, *, status="success", termination="eog", text="한 글!", ids=None):
        return json.dumps(
            {
                "schema_version": 1,
                "status": status,
                "termination": termination,
                "input_samples": 1600,
                "text_utf8_hex": text.encode().hex(),
                "token_ids": [1, 2] if ids is None else ids,
            }
        ).encode()

    def test_loaded_once_and_same_pcm_is_forwarded_without_file_conversion(self):
        adapter = self.load()
        self.library.edge_gguf_transcribe.return_value = self.result()
        self.assertEqual(adapter.transcribe(self.audio), "한 글!")
        args = self.library.edge_gguf_transcribe.call_args.args
        self.assertEqual((args[0], args[2], args[1][0]), (1234, 1600, 0.25))
        self.assertEqual(adapter.last_result_metadata["raw_text"], "한 글!")
        self.assertEqual(adapter.metadata["provider"], "cpu")
        with self.assertRaises(RuntimeError):
            adapter.load()
        adapter.close()
        adapter.close()
        self.library.edge_gguf_destroy.assert_called_once_with(1234)

    def test_generation_limit_repetition_and_context_failures_preserve_raw_output(self):
        adapter = self.load()
        for reason in ("token_limit", "repetition", "context_limit"):
            self.library.edge_gguf_transcribe.return_value = self.result(
                status="failed", termination=reason
            )
            with self.subTest(reason=reason), self.assertRaises(RuntimeError):
                adapter.transcribe(self.audio)
            self.assertEqual(adapter.last_result_metadata["raw_text"], "한 글!")
            self.assertEqual(adapter.last_result_metadata["native_result"]["termination"], reason)
        self.library.edge_gguf_transcribe.return_value = self.result(ids=list(range(128)))
        with self.assertRaises(RuntimeError):
            adapter.transcribe(self.audio)

    def test_invalid_utf8_and_wrong_input_length_are_not_success(self):
        adapter = self.load()
        report = json.loads(self.result())
        report["text_utf8_hex"] = "ff"
        self.library.edge_gguf_transcribe.return_value = json.dumps(report).encode()
        with self.assertRaises(ValueError):
            adapter.transcribe(self.audio)
        self.assertEqual(adapter.last_result_metadata["native_result"]["text_utf8_hex"], "ff")
        report["input_samples"] = 1599
        self.library.edge_gguf_transcribe.return_value = json.dumps(report).encode()
        with self.assertRaises(ValueError):
            adapter.transcribe(self.audio)

    def test_asset_or_binary_receipt_mismatch_rejected_before_loading(self):
        self.build["library_sha256"] = "0" * 64
        (self.root / "build.json").write_text(json.dumps(self.build))
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)
        self.build["library_sha256"] = hashlib.sha256(b"fixture library").hexdigest()
        (self.root / "build.json").write_text(json.dumps(self.build))
        (self.root / "model" / next(iter(self.expected))).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)

    def test_abi_and_create_failure_are_explicit(self):
        adapter = create_adapter(self.model, 16000, self.root)
        self.library.edge_gguf_abi.return_value = 2
        with patch.object(ctypes, "CDLL", return_value=self.library), self.assertRaises(ValueError):
            adapter.load()
        self.library.edge_gguf_abi.return_value = 1
        self.library.edge_gguf_create.return_value = None
        self.library.edge_gguf_error.return_value = b"fixture allocation error"
        with (
            patch.object(ctypes, "CDLL", return_value=self.library),
            self.assertRaisesRegex(RuntimeError, "allocation"),
        ):
            adapter.load()

    def test_unsupported_modes_and_incomplete_runtime_config_are_rejected(self):
        text = (ROOT / "configs/gguf.toml").read_text()
        for value in (
            text.replace('mode = "batch"', 'mode = "paced_stream"'),
            text.replace("runtime_library = ", "wrong_runtime = "),
            text.replace('quantization = "q8_0"', 'quantization = "q4"'),
        ):
            path = self.root / "bad.toml"
            path.write_text(value)
            with self.assertRaises(ValueError):
                load_evaluation_config(path)


@unittest.skipUnless(
    os.environ.get("EDGE_ASR_LLAMA_SOURCE") and shutil.which("g++"),
    "GGUF C ABI 구문 검사는 별도 llama.cpp 소스와 g++가 필요합니다.",
)
class GGUFNativeSyntaxTests(unittest.TestCase):
    def test_bridge_matches_pinned_native_headers_without_loading_a_model(self):
        source = Path(os.environ["EDGE_ASR_LLAMA_SOURCE"]).resolve()
        command = ["g++", "-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Werror"]
        for path in ("include", "ggml/include", "tools/mtmd", "vendor/nlohmann"):
            command += ["-I", str(source / path)]
        command.append(str(ROOT / "native/gguf/bridge.cpp"))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
