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

    def test_operator_profile_requires_separate_build_and_preserves_invalid_trace(self):
        adapter = self.load()
        with self.assertRaisesRegex(ValueError, "별도 빌드"):
            adapter.set_operator_profile(True)
        with self.assertRaises(ValueError):
            adapter.set_operator_profile(1)
        self.build["features"] = ["operator_profile_v1"]
        (self.root / "build.json").write_text(json.dumps(self.build))
        self.library.edge_gguf_set_operator_profile.return_value = 0
        adapter = self.load()
        for enabled in (False, True, False):
            adapter.set_operator_profile(enabled)
            self.library.edge_gguf_set_operator_profile.assert_called_with(1234, int(enabled))
            row = json.loads(self.result())
            row["operator_profile"] = {
                "schema_version": 1,
                "enabled": enabled,
                "valid": True,
                "overflow_groups": 0,
                "groups": [{"op": "MUL_MAT"}] if enabled else [],
            }
            self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
            self.assertEqual(adapter.transcribe(self.audio), "한 글!")
            row["operator_profile"]["overflow_groups"] = 1
            self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
            with self.assertRaisesRegex(ValueError, "집계 용량"):
                adapter.transcribe(self.audio)
            self.assertEqual(adapter.last_result_metadata["native_result"], row)
        self.library.edge_gguf_set_operator_profile.return_value = -1
        with self.assertRaises(RuntimeError):
            adapter.set_operator_profile(True)

    def enable_split_threads(self):
        self.build["features"] = ["split_threads_v1"]
        (self.root / "build.json").write_text(json.dumps(self.build))
        self.library.edge_gguf_create_threads.return_value = 1234

    def enable_prefix_cache(self):
        self.build["features"] = ["text_prefix_cache_v1"]
        (self.root / "build.json").write_text(json.dumps(self.build))
        self.library.edge_gguf_set_prefix_cache.return_value = 0

    def prefix_result(self, *, hit=False):
        row = json.loads(self.result())
        row.update(
            runtime_configuration={"cache_text_prefix": True},
            prefix_cache={
                "enabled": True,
                "hit": hit,
                "prefix_tokens": 3,
                "token_ids": [7, 8, 9],
                "reused_tokens": 3 if hit else 0,
                "miss_reason": None if hit else "cold",
                "valid_after_call": True,
            },
            prefix_eval_s=0 if hit else 0.1,
            suffix_eval_s=0.2,
            audio_encode_s=0.3,
            audio_embedding_eval_s=0.4,
            audio_and_prefill_s=1,
        )
        return row

    def test_prefix_cache_bool_is_optional_and_gguf_only(self):
        original = (ROOT / "configs/gguf.toml").read_text()
        self.assertNotIn("cache_text_prefix", self.model)
        path = self.root / "prefix.toml"
        for value in ("true", "false", "1", '"true"'):
            path.write_text(original.replace("[audio]", f"cache_text_prefix = {value}\n\n[audio]"))
            if value in ("true", "false"):
                self.assertIs(
                    load_evaluation_config(path)["model"]["cache_text_prefix"], value == "true"
                )
            else:
                with self.assertRaises(ValueError):
                    load_evaluation_config(path)
        onnx = (ROOT / "configs/onnx.toml").read_text()
        path.write_text(onnx.replace("[audio]", "cache_text_prefix = true\n\n[audio]"))
        with self.assertRaisesRegex(ValueError, "알 수 없는"):
            load_evaluation_config(path)
        self.model["cache_text_prefix"] = 1
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)

    def test_prefix_capability_symbol_and_explicit_mode_are_required(self):
        for mode in (False, True):
            self.model["cache_text_prefix"] = mode
            with self.assertRaisesRegex(ValueError, "text_prefix_cache_v1"):
                create_adapter(self.model, 16000, self.root)
        self.enable_prefix_cache()
        self.model.pop("cache_text_prefix")
        self.load()
        self.library.edge_gguf_set_prefix_cache.assert_not_called()
        for mode in (False, True):
            self.model["cache_text_prefix"] = mode
            adapter = self.load()
            self.library.edge_gguf_set_prefix_cache.assert_called_with(1234, int(mode))
            self.assertIs(adapter.metadata["cache_text_prefix"], mode)
            row = self.prefix_result()
            row["runtime_configuration"]["cache_text_prefix"] = mode
            row["prefix_cache"].update(
                enabled=mode, valid_after_call=mode, miss_reason="cold" if mode else "disabled"
            )
            self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
            self.assertEqual(adapter.transcribe(self.audio), "한 글!")
        del self.library.edge_gguf_set_prefix_cache
        with self.assertRaisesRegex(ValueError, "심볼"):
            self.load()

    def test_prefix_setup_failure_closes_handle(self):
        self.enable_prefix_cache()
        self.model["cache_text_prefix"] = True
        self.library.edge_gguf_set_prefix_cache.return_value = -1
        with self.assertRaisesRegex(RuntimeError, "설정 실패"):
            self.load()
        self.library.edge_gguf_destroy.assert_called_once_with(1234)

    def test_prefix_profile_is_checked_and_failure_invalidates_without_erasing_raw(self):
        self.enable_prefix_cache()
        self.model["cache_text_prefix"] = True
        adapter = self.load()
        for hit in (False, True):
            row = self.prefix_result(hit=hit)
            self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
            self.assertEqual(adapter.transcribe(self.audio), "한 글!")
        bad = [self.prefix_result(hit=True) for _ in range(5)]
        bad[0]["runtime_configuration"]["cache_text_prefix"] = False
        bad[1]["prefix_cache"]["reused_tokens"] = 0
        bad[2]["prefix_eval_s"] = 0.1
        bad[3]["audio_encode_s"] = -1
        bad[4]["prefix_cache"]["token_ids"] = []
        for row in bad:
            with self.subTest(row=row):
                before = self.library.edge_gguf_set_prefix_cache.call_count
                self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
                with self.assertRaises(ValueError):
                    adapter.transcribe(self.audio)
                self.assertEqual(self.library.edge_gguf_set_prefix_cache.call_count, before + 1)
                self.assertEqual(adapter.last_result_metadata["raw_text"], "한 글!")
                self.assertEqual(adapter.last_result_metadata["native_result"], row)

    def test_prefix_reset_failure_closes_model_and_keeps_failure_evidence(self):
        self.enable_prefix_cache()
        self.model["cache_text_prefix"] = True
        adapter = self.load()
        self.library.edge_gguf_set_prefix_cache.return_value = -1
        self.library.edge_gguf_transcribe.return_value = self.result(
            status="failed", termination="callback_error"
        )
        with self.assertRaises(RuntimeError) as caught:
            adapter.transcribe(self.audio)
        self.assertIsNone(adapter.handle)
        self.assertEqual(adapter.last_result_metadata["raw_text"], "한 글!")
        self.assertIn("복구 실패", caught.exception.__notes__[0])

    def test_split_threads_forwarded_and_absent_option_keeps_legacy_abi(self):
        self.enable_split_threads()
        adapter = self.load()
        self.library.edge_gguf_create.assert_called_once()
        self.library.edge_gguf_create_threads.assert_not_called()
        self.assertEqual(adapter.metadata["thread_configuration"]["audio_threads"], 2)
        self.library.edge_gguf_create.reset_mock()
        for generation, batch in ((3, 3), (2, 3)):
            with self.subTest(generation=generation, batch=batch):
                self.model.update(num_threads=generation, num_threads_batch=batch)
                adapter = self.load()
                self.assertEqual(
                    self.library.edge_gguf_create_threads.call_args.args[2:],
                    (generation, batch, 512, 128),
                )
                self.assertEqual(adapter.metadata["thread_configuration"]["audio_threads"], batch)
        self.library.edge_gguf_create.assert_not_called()

    def test_split_threads_require_capability_and_export_before_creation(self):
        self.model["num_threads_batch"] = 3
        with self.assertRaisesRegex(ValueError, "split_threads_v1"):
            create_adapter(self.model, 16000, self.root)
        self.enable_split_threads()
        del self.library.edge_gguf_create_threads
        with self.assertRaisesRegex(ValueError, "심볼"):
            self.load()
        self.library.edge_gguf_create.assert_not_called()

    def test_runtime_thread_mismatch_preserves_raw_result(self):
        self.enable_split_threads()
        self.model["num_threads_batch"] = 3
        adapter = self.load()
        row = json.loads(self.result())
        row["runtime_configuration"] = {"threads": 2, "threads_batch": 3, "audio_threads": 3}
        self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
        self.assertEqual(adapter.transcribe(self.audio), "한 글!")
        for actual in ({}, {"threads": 3}, {"threads": 2, "threads_batch": 3, "audio_threads": 2}):
            with self.subTest(actual=actual):
                row["runtime_configuration"] = actual
                self.library.edge_gguf_transcribe.return_value = json.dumps(row).encode()
                with self.assertRaisesRegex(ValueError, "실제 스레드"):
                    adapter.transcribe(self.audio)
                self.assertEqual(adapter.last_result_metadata["raw_text"], "한 글!")
                self.assertEqual(adapter.last_result_metadata["native_result"], row)

    def test_optional_batch_threads_are_positive_and_gguf_only(self):
        source = (ROOT / "configs/gguf.toml").read_text()
        self.assertNotIn(
            "num_threads_batch", load_evaluation_config(ROOT / "configs/gguf.toml")["model"]
        )
        path = self.root / "threads.toml"
        for value in ("3", "0", "-1", "true", "2.5", '"3"'):
            with self.subTest(value=value):
                path.write_text(
                    source.replace(
                        "num_threads = 2", f"num_threads = 2\nnum_threads_batch = {value}"
                    )
                )
                if value == "3":
                    self.assertEqual(load_evaluation_config(path)["model"]["num_threads_batch"], 3)
                else:
                    with self.assertRaises(ValueError):
                        load_evaluation_config(path)
        onnx = (ROOT / "configs/onnx.toml").read_text()
        path.write_text(onnx.replace("num_threads = 2", "num_threads = 2\nnum_threads_batch = 3"))
        with self.assertRaisesRegex(ValueError, "알 수 없는"):
            load_evaluation_config(path)
        self.model["num_threads_batch"] = True
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)

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

    def audio_q4_assets(self):
        directory = self.root / "model"
        text, audio = directory / "text-q4.gguf", directory / "audio-q4.gguf"
        text.write_bytes(b"fixture text q4")
        audio.write_bytes(b"fixture audio q4")
        audio_hash = hashlib.sha256(audio.read_bytes()).hexdigest()
        expected = patch.object(gguf, "AUDIO_Q4_SHA256", audio_hash)
        expected.start()
        self.addCleanup(expected.stop)
        assets = {
            "schema_version": 2,
            "quantization": "q4_k_m",
            "source_sha256": gguf.BF16_SHA256,
            "llama_commit": gguf.LLAMA_COMMIT,
            "text": {"path": text.name, "sha256": hashlib.sha256(text.read_bytes()).hexdigest()},
            "audio": {
                "path": audio.name,
                "sha256": audio_hash,
                "quantization": "q4_0",
                "source_sha256": gguf.AUDIO_BF16_SHA256,
                "reference_q8_sha256": self.expected["mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"],
            },
        }
        path = directory / "both-q4.json"
        path.write_text(json.dumps(assets))
        self.model.update(
            quantization="q4_k_m", audio_quantization="q4_0", asset_manifest=path.name
        )
        return path, assets, text, audio

    def test_both_q4_uses_separately_bound_audio_and_reports_its_precision(self):
        path, _, text, audio = self.audio_q4_assets()
        adapter = self.load()
        self.assertEqual(
            self.library.edge_gguf_create.call_args.args[:2],
            (str(text).encode(), str(audio).encode()),
        )
        self.assertEqual(adapter.metadata["audio_quantization"], "q4_0")
        self.assertEqual(adapter.model_files["asset_manifest"], path)
        self.assertEqual(adapter.model_files["audio"], audio)

    def test_audio_q4_cannot_bypass_schema_source_or_payload_hash_guards(self):
        path, assets, _, audio = self.audio_q4_assets()
        for field, value in (
            ("quantization", "q8_0"),
            ("source_sha256", "0" * 64),
            ("sha256", "0" * 64),
            ("reference_q8_sha256", "0" * 64),
        ):
            with self.subTest(field=field):
                path.write_text(json.dumps({**assets, "audio": {**assets["audio"], field: value}}))
                with self.assertRaises(ValueError):
                    create_adapter(self.model, 16000, self.root)
        path.write_text(json.dumps({**assets, "schema_version": 1}))
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)
        path.write_text(json.dumps(assets))
        self.model.pop("audio_quantization")
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)
        self.model["audio_quantization"] = "q4_0"
        audio.write_bytes(b"tampered audio")
        with self.assertRaises(ValueError):
            create_adapter(self.model, 16000, self.root)

    def test_audio_quantization_is_explicit_validated_and_requires_manifest(self):
        original = (ROOT / "configs/gguf.toml").read_text()
        path = self.root / "audio-config.toml"
        text = original.replace("[audio]", 'audio_quantization = "q4_0"\n\n[audio]')
        path.write_text(text)
        with self.assertRaises(ValueError):
            load_evaluation_config(path)
        text = text.replace("[audio]", 'asset_manifest = "both-q4.json"\n\n[audio]')
        path.write_text(text)
        self.assertEqual(load_evaluation_config(path)["model"]["audio_quantization"], "q4_0")
        path.write_text(
            text.replace('audio_quantization = "q4_0"', 'audio_quantization = "q4_k_m"')
        )
        with self.assertRaises(ValueError):
            load_evaluation_config(path)


@unittest.skipUnless(shutil.which("g++"), "text prefix KV 상태 검사는 g++가 필요합니다.")
class GGUFPolicyTests(unittest.TestCase):
    def test_only_matching_prefix_survives_and_all_failure_paths_clear_state(self):
        code = r"""
#include "prefix_cache.h"
#include <cassert>
#include <string>
int main() {
    TextPrefixCache cache;
    std::vector<int32_t> prefix{7,8,9}, memory{1,2};
    int clears=0, trims=0;
    bool allow_trim=true;
    auto clear=[&]{ ++clears; memory.clear(); };
    auto trim=[&](size_t n){
        ++trims;
        assert(n==prefix.size());
        if (!allow_trim) return false;
        memory.resize(n);
        return true;
    };
    cache.enabled=true;
    assert(std::string(cache.begin(prefix,clear,trim))=="cold");
    assert(clears==1 && trims==0 && memory.empty());
    memory=prefix; memory.push_back(100); memory.push_back(200);
    cache.complete(prefix,true);
    assert(cache.begin(prefix,clear,trim)==nullptr);
    assert(memory==prefix && clears==1 && trims==1);
    cache.complete(prefix,false); // callback/decoder/EOG 실패
    assert(!cache.valid());
    assert(std::string(cache.begin(prefix,clear,trim))=="cold");
    assert(memory.empty());
    memory=prefix; cache.complete(prefix,true);
    assert(std::string(cache.begin({4,5,6},clear,trim))=="prefix_changed");
    assert(!cache.valid() && memory.empty());
    memory=prefix; cache.complete(prefix,true); allow_trim=false;
    assert(std::string(cache.begin(prefix,clear,trim))=="trim_failed");
    assert(!cache.valid() && memory.empty());
    memory=prefix; cache.complete(prefix,true);
    assert(std::string(cache.begin({},clear,trim))=="empty_prefix");
    assert(!cache.valid() && memory.empty());
    memory=prefix; cache.complete(prefix,true); cache.enabled=false;
    assert(std::string(cache.begin(prefix,clear,trim))=="disabled");
    assert(!cache.valid() && memory.empty() && trims==2);
}
"""
        self.compile_and_run(code)

    def test_operator_profile_pairs_callbacks_bounds_memory_and_tracks_shape_changes(self):
        self.compile_and_run(r"""
#include "operator_profile.h"
#include <cassert>
int main() {
    OperatorProfile p; OperatorNode n; n.name="layer.0"; n.op="MUL_MAT";
    n.lhs_type="q4_0"; n.shape={2,3,1,1}; int tensor=0;
    p.enabled=true; p.phase="audio_encoder";
    p.before(&tensor,100); p.after(&tensor,130,n);
    n.shape[1]=4; p.before(&tensor,200); p.after(&tensor,250,n);
    assert(p.complete() && p.groups.size()==1);
    const auto& sample=p.groups.begin()->second;
    assert(sample.count==2 && sample.total_ns==80 && sample.max_ns==50 && sample.shape_changed);
    p.before(&tensor,300); assert(!p.complete());
    p.after(nullptr,400,n); assert(!p.complete());
    p.reset(); assert(p.complete() && p.enabled && p.groups.empty());
    p.before(&tensor,100); p.after(&tensor,99,n); assert(!p.complete());
    p.reset();
    for(int i=0;i<8193;++i) {
        n.name=std::to_string(i); p.before(&tensor,100); p.after(&tensor,110,n);
    }
    assert(p.groups.size()==8192 && p.overflow==1 && !p.complete());
}
""")

    def compile_and_run(self, code):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "prefix.cpp"
            binary = Path(directory) / "prefix"
            source.write_text(code)
            result = subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "native/gguf"),
                    str(source),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


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
        for profile in (False, True):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    command + (["-DEDGE_ASR_PROFILE_OPERATORS"] if profile else []),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
