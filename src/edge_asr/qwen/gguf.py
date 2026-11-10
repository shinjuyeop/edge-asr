"""해시로 고정한 GGUF 자산과 별도 llama.cpp CPU C ABI를 사용하는 어댑터."""

import ctypes
import json
from array import array

from edge_asr.artifacts import digest
from edge_asr.dataset import Audio, resolve_path
from edge_asr.runtime import capture_native_stderr

LLAMA_COMMIT = "7d7f0287530bc8981690f361d8c1627dbae1c99a"
BF16_SHA256 = "12b2894d9a7c98cd8f26670f5a47ab738f42bcc98df21e109be493870c71ba50"
QUANTIZATIONS = ("q8_0", "q6_k", "q5_k_m", "q4_k_m")
MODEL_FILES = {
    "Qwen3-ASR-0.6B-Q8_0.gguf": "bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971",
    "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf": "41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d",
}


class Qwen3GGUFAdapter:
    def __init__(self, model, sample_rate_hz, root):
        if sample_rate_hz != 16000 or model.get("language") != "ko":
            raise ValueError("GGUF 비교 경로는 한국어 ko·16000Hz 고정입니다.")
        if model.get("quantization") not in QUANTIZATIONS:
            raise ValueError("지원하는 GGUF 양자화가 아닙니다.")
        self.model = model
        self.directory = resolve_path(model["path"], root)
        self.library_path = resolve_path(model["runtime_library"], root)
        self.manifest_path = resolve_path(model["runtime_manifest"], root)
        expected = dict(MODEL_FILES)
        self.assets = None
        if model.get("asset_manifest"):
            assets_path = resolve_path(model["asset_manifest"], self.directory)
            self.assets = json.loads(assets_path.read_text())
            a = self.assets
            if (
                a.get("schema_version") != 1
                or a.get("quantization") != model["quantization"]
                or a.get("source_sha256") != BF16_SHA256
                or a.get("llama_commit") != LLAMA_COMMIT
                or a.get("audio", {}).get("sha256")
                != MODEL_FILES["mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"]
            ):
                raise ValueError("GGUF 자산 명세의 양자화·원본·오디오·도구 커밋이 다릅니다.")
            for role in ("text", "audio"):
                row = a.get(role, {})
                if (
                    not isinstance(row.get("path"), str)
                    or not row["path"].strip()
                    or not isinstance(row.get("sha256"), str)
                    or len(row["sha256"]) != 64
                ):
                    raise ValueError("GGUF text/audio 경로와 SHA-256이 필요합니다.")
            self.model_files = {
                role: resolve_path(a[role]["path"], self.directory) for role in ("text", "audio")
            }
            expected = {role: a[role]["sha256"] for role in ("text", "audio")}
        else:
            if model["quantization"] != "q8_0":
                raise ValueError("새 양자화에는 asset_manifest가 필요합니다.")
            self.model_files = {name: self.directory / name for name in MODEL_FILES}
        self.text_path, self.audio_path = self.model_files.values()
        for name, path in self.model_files.items():
            if digest(path) != expected[name]:
                raise ValueError(f"확인한 GGUF 자산과 SHA-256이 다릅니다: {name}")
        if self.assets is not None:
            self.model_files["asset_manifest"] = assets_path
        self.build = json.loads(self.manifest_path.read_text())
        if self.build.get("llama_commit") != LLAMA_COMMIT or self.build.get(
            "library_sha256"
        ) != digest(self.library_path):
            raise ValueError("GGUF 실행 라이브러리의 커밋/빌드 해시가 다릅니다.")
        self.model_files.update(
            runtime_library=self.library_path, runtime_manifest=self.manifest_path
        )
        self.handle, self.library, self.last_result_metadata = None, None, None

    @property
    def metadata(self):
        return {
            "backend": "llama_cpp_mtmd",
            "provider": "cpu",
            "supported_modes": ["batch"],
            "llama_commit": LLAMA_COMMIT,
            "language": "ko",
            "stream_language": "Korean",
            "quantization": self.model["quantization"],
            "quantization_evidence": "local_BF16_conversion_and_bound_sha256"
            if self.assets
            else "official_GGUF_asset_names_and_bound_sha256",
            "quantization_scope": "text만 양자화 변경·audio mmproj Q8_0 유지. tensor 혼합 dtype은 자산 명세에 기록."
            if self.assets
            else "텍스트 모델과 오디오 mmproj 모두 Q8_0 배포본. 일부 tensor 혼합 dtype 가능. ONNX INT8과 동일 양자화 방식 아님.",
            "assets": self.assets,
            "max_total_len": self.model["max_total_len"],
            "max_new_tokens": self.model["max_new_tokens"],
            "prompt_policy": "empty_system_audio_user_forced_assistant_language_Korean_asr_text_v1",
            "output_policy": "gguf_raw_utf8_until_eog_v1",
            "sampling": "greedy",
            "repetition_policy": "last64_token_ids_at_most3_distinct_failure",
            "use_itn": None,
            "context_policy": "mtmd_positions_plus_reserved_output_no_truncation_v1",
            "input_policy": "동일 float32 PCM을 직접 전달. file codec/resampling 없음",
            "runtime_build": self.build,
        }

    def load(self):
        if self.handle is not None:
            raise RuntimeError("이미 로드한 GGUF 모델입니다.")
        self.library = ctypes.CDLL(str(self.library_path))
        lib = self.library
        lib.edge_gguf_abi.restype = ctypes.c_int
        lib.edge_gguf_commit.restype = ctypes.c_char_p
        lib.edge_gguf_error.restype = ctypes.c_char_p
        lib.edge_gguf_create.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.edge_gguf_create.restype = ctypes.c_void_p
        lib.edge_gguf_transcribe.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
        ]
        lib.edge_gguf_transcribe.restype = ctypes.c_char_p
        lib.edge_gguf_destroy.argtypes = [ctypes.c_void_p]
        lib.edge_gguf_destroy.restype = None
        if lib.edge_gguf_abi() != 1 or lib.edge_gguf_commit().decode() != LLAMA_COMMIT:
            raise ValueError("GGUF C ABI/커밋 불일치")
        self.handle = lib.edge_gguf_create(
            str(self.text_path).encode(),
            str(self.audio_path).encode(),
            self.model["num_threads"],
            self.model["max_total_len"],
            self.model["max_new_tokens"],
        )
        if not self.handle:
            raise RuntimeError(
                "GGUF 모델 로딩 실패: " + lib.edge_gguf_error().decode(errors="replace")
            )

    def transcribe(self, audio):
        self.last_result_metadata = None
        if self.handle is None:
            raise RuntimeError("모델을 먼저 로드해야 합니다.")
        if (
            not isinstance(audio, Audio)
            or not isinstance(audio.samples, array)
            or audio.samples.typecode != "f"
        ):
            raise TypeError("Audio의 float32 PCM 배열이 필요합니다.")
        if audio.sample_rate_hz != 16000 or len(audio.samples) < 320:
            raise ValueError("16000Hz·최소 320프레임 입력이 필요합니다.")
        pcm = (ctypes.c_float * len(audio.samples)).from_buffer(audio.samples)
        self.last_result_metadata = {"output_policy": "gguf_raw_utf8_until_eog_v1"}
        with capture_native_stderr(self.last_result_metadata):
            blob = self.library.edge_gguf_transcribe(self.handle, pcm, len(audio.samples))
        if not blob:
            raise RuntimeError("GGUF C ABI가 결과를 반환하지 않았습니다.")
        result = json.loads(blob)
        self.last_result_metadata["native_result"] = result
        if result.get("schema_version") != 1 or result.get("input_samples") != len(audio.samples):
            raise ValueError("GGUF 반환 schema/입력 길이 불일치")
        try:
            text = bytes.fromhex(result["text_utf8_hex"]).decode("utf-8")
        except (KeyError, ValueError, UnicodeError) as error:
            raise ValueError("GGUF 출력이 완전한 UTF-8이 아닙니다.") from error
        tokens = result.get("token_ids")
        if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
            raise TypeError("GGUF token ID 반환 형식 오류")
        self.last_result_metadata.update(
            raw_text=text, token_ids=tokens, returned_token_count=len(tokens)
        )
        if (
            result.get("status") != "success"
            or result.get("termination") != "eog"
            or len(tokens) >= self.model["max_new_tokens"]
        ):
            raise RuntimeError(
                f"GGUF 전사 실패/잘림: {result.get('termination')}: {result.get('error')}"
            )
        return text

    def close(self):
        if self.handle is not None:
            self.library.edge_gguf_destroy(self.handle)
            self.handle = None
        self.last_result_metadata = None
