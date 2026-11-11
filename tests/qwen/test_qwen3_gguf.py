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

    def enable_tokens(self):
        self.build["features"] = ["token_callback_v1"]
        (self.root / "build.json").write_text(json.dumps(self.build))

    def test_token_bytes_wait_for_complete_utf8_and_preserve_final_output(self):
        self.enable_tokens()
        adapter, updates = self.load(), []
        raw = "한 글!".encode()
        pieces = [raw[:1], raw[1:2], raw[2:3], raw[3:]]

        def generate(_handle, _pcm, _count, callback, user):
            for index, value in enumerate(pieces, 1):
                buffer = ctypes.create_string_buffer(value)
                self.assertEqual(
                    callback(user, ctypes.addressof(buffer), len(value), index, index / 10), 0
                )
            return self.result(ids=[1, 2, 3, 4])

        self.library.edge_gguf_transcribe_tokens.side_effect = generate
        self.assertEqual(adapter.transcribe_stream(self.audio, updates.append), "한 글!")
        self.assertEqual([u["hypothesis"] for u in updates], ["", "", "한", "한 글!"])
        self.assertEqual(b"".join(bytes.fromhex(u["piece_hex"]) for u in updates), raw)
        self.assertTrue(adapter.metadata["token_output_callback"])

    def test_token_callback_copies_counted_bytes_including_nul(self):
        self.enable_tokens()
        adapter, updates = self.load(), []
        raw = "한\0글".encode()

        def generate(_handle, _pcm, _count, callback, user):
            buffer = ctypes.create_string_buffer(raw)
            self.assertEqual(callback(user, ctypes.addressof(buffer), len(raw), 1, 0.1), 0)
            return self.result(text="한\0글", ids=[1])

        self.library.edge_gguf_transcribe_tokens.side_effect = generate
        self.assertEqual(adapter.transcribe_stream(self.audio, updates.append), "한\0글")
        self.assertEqual(updates[0]["piece_hex"], raw.hex())

    def test_token_callback_failure_does_not_cross_ffi_or_erase_raw_output(self):
        self.enable_tokens()
        adapter = self.load()

        def generate(_handle, _pcm, _count, callback, user):
            raw = "한".encode()
            buffer = ctypes.create_string_buffer(raw)
            self.assertEqual(callback(user, ctypes.addressof(buffer), len(raw), 1, 0.1), 1)
            return self.result(text="한", ids=[1], status="failed", termination="callback_error")

        def broken(_update):
            raise OSError("fixture display failure")

        self.library.edge_gguf_transcribe_tokens.side_effect = generate
        with self.assertRaisesRegex(RuntimeError, "수신/표시"):
            adapter.transcribe_stream(self.audio, broken)
        self.assertEqual(adapter.last_result_metadata["raw_text"], "한")
        self.assertEqual(
            adapter.last_result_metadata["native_result"]["termination"], "callback_error"
        )
        self.library.edge_gguf_transcribe.return_value = self.result()
        self.assertEqual(adapter.transcribe(self.audio), "한 글!")

    def test_token_stream_rejects_missing_capability_and_result_mismatch(self):
        adapter = self.load()
        with self.assertRaises(ValueError):
            adapter.transcribe_stream(self.audio, lambda _: None)
        self.library.edge_gguf_transcribe_tokens.assert_not_called()
        self.enable_tokens()
        adapter = self.load()

        def generate(_handle, _pcm, _count, callback, user):
            raw = "한".encode()
            buffer = ctypes.create_string_buffer(raw)
            callback(user, ctypes.addressof(buffer), len(raw), 1, 0.1)
            return self.result(text="다른", ids=[1])

        self.library.edge_gguf_transcribe_tokens.side_effect = generate
        with self.assertRaisesRegex(ValueError, "최종 전사"):
            adapter.transcribe_stream(self.audio, lambda _: None)
        self.assertEqual(adapter.last_result_metadata["raw_text"], "다른")

    def test_token_receipt_without_exported_symbol_is_rejected(self):
        self.enable_tokens()
        del self.library.edge_gguf_transcribe_tokens
        with self.assertRaisesRegex(ValueError, "심볼"):
            self.load()
        self.library.edge_gguf_create.assert_not_called()

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

    def test_quantized_manifest_selects_assets_and_rejects_wrong_provenance(self):
        audio_name = "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
        for quant in ("q6_k", "q5_k_m", "q4_k_m"):
            text = self.root / "model" / (quant + ".gguf")
            text.write_bytes(b"fixture quantized weights")
            assets = {
                "schema_version": 1,
                "quantization": quant,
                "source_sha256": gguf.BF16_SHA256,
                "llama_commit": gguf.LLAMA_COMMIT,
                "text": {
                    "path": text.name,
                    "sha256": hashlib.sha256(text.read_bytes()).hexdigest(),
                },
                "audio": {"path": audio_name, "sha256": self.expected[audio_name]},
            }
            path = self.root / "model/assets.json"
            path.write_text(json.dumps(assets))
            self.model.update(quantization=quant, asset_manifest="assets.json")
            adapter = self.load()
            self.assertEqual(adapter.metadata["quantization"], quant)
            self.assertEqual(self.library.edge_gguf_create.call_args.args[0], str(text).encode())
            self.assertEqual(adapter.model_files["asset_manifest"], path)
            for field, value in (
                ("source_sha256", "0" * 64),
                ("quantization", "q8_0"),
                ("llama_commit", "other"),
            ):
                changed = {**assets, field: value}
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    create_adapter(self.model, 16000, self.root)
            path.write_text(json.dumps(assets))
            text.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                create_adapter(self.model, 16000, self.root)

    def test_lower_quantization_requires_manifest_in_config(self):
        original = (ROOT / "configs/gguf.toml").read_text()
        for quant in ("q6_k", "q5_k_m", "q4_k_m"):
            path = self.root / "quant.toml"
            text = original.replace('quantization = "q8_0"', f'quantization = "{quant}"')
            path.write_text(text)
            with self.assertRaises(ValueError):
                load_evaluation_config(path)
            path.write_text(text.replace("[audio]", 'asset_manifest = "assets.json"\n\n[audio]'))
            self.assertEqual(load_evaluation_config(path)["model"]["quantization"], quant)


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
