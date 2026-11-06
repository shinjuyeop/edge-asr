"""확인한 Qwen3-ASR INT8 배포본의 Offline 어댑터. 길이 한계로 잘린 전사를 거부한다."""

import os
import tempfile
from array import array
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.dataset import Audio, resolve_path

# 입력 길이 계산은 이 자산과 sherpa 2.0.1의 centered-STFT/100-frame chunk에 한정한다.
MODEL_FILES = {
    "conv_frontend.onnx": "d22dc4423e0940e49884e903d2ea2f7e5567c14fc1aed97e4e26d6b8f208ef9e",
    "encoder.int8.onnx": "60748d3e6744a57c9c91e1b17424a6c2990567e8adceb0783940c03ed98fa9d9",
    "decoder.int8.onnx": "4f6885be5959ae26af3089d38ee7972c5fafbeeb1cf8d5e76eab6d8b61ca5771",
    "tokenizer/vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
    "tokenizer/merges.txt": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
    "tokenizer/tokenizer_config.json": "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c",
}
PROMPT = (
    "<|im_start|>system\n<|im_end|>\n<|im_start|>user\n<|audio_start|>"
    "<|audio_end|><|im_end|>\n<|im_start|>assistant\nlanguage Korean<asr_text>"
)
# byte-BPE는 각 UTF-8 byte에서 시작하여 merge한다. special token도 byte 수보다 작다.
# 실제 prompt token 수가 아닌 안전한 상한. audio_pad는 확인한 자산에서 단일 special token.
PROMPT_TOKEN_UPPER_BOUND = len(PROMPT.encode("utf-8"))
OUTPUT_POLICY = "sherpa_qwen3_native_text_v1"


def context_budget(sample_count, max_new_tokens, max_total_len):
    # v2.0.1 offline-stream.cc: centered STFT의 frame 수 = floor(samples/160).
    frames = sample_count // 160
    full, remainder = divmod(frames, 100)
    audio_tokens = full * 13 + (remainder + 7) // 8
    upper = PROMPT_TOKEN_UPPER_BOUND + audio_tokens
    return {
        "feature_frames": frames,
        "audio_tokens_upper_bound": audio_tokens,
        "prompt_tokens_upper_bound": PROMPT_TOKEN_UPPER_BOUND,
        "context_tokens_upper_bound": upper,
        "reserved_output_tokens": max_new_tokens + 1,
        "max_total_len": max_total_len,
        "fits": upper + max_new_tokens + 1 <= max_total_len,
    }


class Qwen3Adapter:
    def __init__(self, model: dict, sample_rate_hz: int, root: Path):
        if sample_rate_hz != 16000 or model.get("language") != "ko":
            raise ValueError("현재 Qwen 검증 경로는 16000Hz·한국어 고정입니다.")
        self.model = model
        self.sample_rate_hz = sample_rate_hz
        self.directory = resolve_path(model["path"], root)
        self.model_files = {name: self.directory / name for name in MODEL_FILES}
        # 자산 검증은 model_load 타이밍 전에 수행한다. 미확인 변환본에 길이 규칙을 대입하지 않는다.
        for name, path in self.model_files.items():
            if digest(path) != MODEL_FILES[name]:
                raise ValueError(f"검증한 Qwen 배포 자산과 SHA-256이 다릅니다: {name}")
        self.recognizer = None
        self.last_result_metadata = None

    @property
    def metadata(self):
        return {
            "backend": "sherpa_onnx",
            "factory": "OfflineRecognizer.from_qwen3_asr",
            "supported_modes": ["batch"],
            "provider": "cpu",
            "feature_dim": 128,
            "language": "ko",
            "stream_language": "Korean",
            "use_itn": None,
            "use_itn_reason": "선정 Qwen API에 ITN 스위치 없음. 모델 고유 표기 유지",
            "quantization": "int8",
            "quantization_evidence": "sha256_bound_onnx_graph_inspection",
            "graph_precision": {
                "conv_frontend": "float32",
                "encoder": "float32_int8_DynamicQuantizeLinear_MatMulInteger",
                "decoder": "float32_uint8_DynamicQuantizeLinear_MatMulInteger",
            },
            "quantization_method": None,
            "quantization_method_reason": "그래프 연산·dtype는 확인. 배포 자산의 생성 절차 전체는 미검증",
            "max_total_len": self.model["max_total_len"],
            "max_new_tokens": self.model["max_new_tokens"],
            "temperature": 1e-6,
            "top_p": 0.8,
            "seed": 42,
            "hotwords": "",
            "tail_padding_s": None,
            "output_policy": OUTPUT_POLICY,
            "native_output_scope": "native의 prefix·반복 tail·UTF-8 정리 이후 반환 text/tokens",
            "context_policy": "sherpa_2.0.1_centered_frames_byte_prompt_upper_bound_v1",
            "stderr_capture_scope": "동일 프로세스에서 순차 전사. decode 중 fd2를 보존·복원하며 원문 재출력",
        }

    def load(self):
        if self.recognizer is not None:
            raise RuntimeError("이미 로드한 Qwen 모델입니다.")
        import numpy as np
        import sherpa_onnx

        if sherpa_onnx.__version__ != "2.0.1":
            raise ValueError("입력 보존 규칙을 검증한 sherpa-onnx 2.0.1이 필요합니다.")
        self.np = np
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=str(self.model_files["conv_frontend.onnx"]),
            encoder=str(self.model_files["encoder.int8.onnx"]),
            decoder=str(self.model_files["decoder.int8.onnx"]),
            tokenizer=str(self.directory / "tokenizer"),
            num_threads=self.model["num_threads"],
            provider="cpu",
            sample_rate=16000,
            feature_dim=128,
            decoding_method="greedy_search",
            max_total_len=self.model["max_total_len"],
            max_new_tokens=self.model["max_new_tokens"],
            temperature=1e-6,
            top_p=0.8,
            seed=42,
            hotwords="",
        )

    def transcribe(self, audio: Audio) -> str:
        self.last_result_metadata = None
        if self.recognizer is None:
            raise RuntimeError("모델을 먼저 로드해야 합니다.")
        if (
            not isinstance(audio, Audio)
            or not isinstance(audio.samples, array)
            or audio.samples.typecode != "f"
        ):
            raise TypeError("Audio의 float PCM 배열이 필요합니다.")
        if audio.sample_rate_hz != 16000 or not audio.samples:
            raise ValueError("비어 있지 않은 16000Hz 오디오가 필요합니다.")
        budget = context_budget(
            len(audio.samples), self.model["max_new_tokens"], self.model["max_total_len"]
        )
        self.last_result_metadata = {"context_budget": budget, "output_policy": OUTPUT_POLICY}
        if not budget["fits"]:
            raise ValueError("Qwen context 길이 상한 초과: 원본 입력을 자르지 않고 거부합니다.")
        if budget["feature_frames"] < 2:
            raise ValueError("Qwen 특징 프레임이 2개 미만입니다.")
        stream = self.recognizer.create_stream()
        stream.set_option("language", "Korean")
        stream.accept_waveform(16000, self.np.frombuffer(audio.samples, dtype=self.np.float32))
        # Native 경고는 Python redirect_stderr로 잡히지 않으므로 실제 fd2를 수집한다.
        # 공통 batch는 순차 호출이고 Refine도 모델 전용 자식 프로세스를 사용한다.
        with tempfile.TemporaryFile() as capture:
            original = os.dup(2)
            try:
                os.dup2(capture.fileno(), 2)
                self.recognizer.decode_stream(stream)
            finally:
                os.dup2(original, 2)
                os.close(original)
                capture.seek(0)
                stderr = capture.read().decode("utf-8", errors="replace")
                self.last_result_metadata["native_stderr"] = stderr
                if stderr:
                    # 외부 실행 console에도 원래 진단을 보존한다.
                    with os.fdopen(os.dup(2), "w", encoding="utf-8") as log:
                        log.write(stderr)
        text, tokens = stream.result.text, list(stream.result.tokens)
        self.last_result_metadata.update(
            raw_text=text, tokens=tokens, returned_token_count=len(tokens)
        )
        if not isinstance(text, str) or any(not isinstance(t, str) for t in tokens):
            raise TypeError("Qwen text/tokens의 반환 형식이 다릅니다.")
        if len(tokens) >= self.model["max_new_tokens"] or any(
            word in stderr.lower()
            for word in ("truncat", "exceeds max_total_len", "no room for audio", "repetition loop")
        ):
            raise RuntimeError(
                "Qwen 출력 잘림/반복 경고 또는 token 상한 도달. raw 결과를 보존합니다."
            )
        return text

    def close(self):
        self.recognizer = None
        self.last_result_metadata = None
