"""SenseVoiceSmall INT8의 sherpa-onnx Offline batch 어댑터."""

import re
from array import array
from pathlib import Path

from edge_asr.dataset import Audio, resolve_path

# SenseVoice의 예약 제어 토큰 문법만 분리한다. 일반 괄호·문장·띄어쓰기는 유지한다.
CONTROL_TOKEN = re.compile(r"<\|[A-Za-z][A-Za-z0-9_]*\|>")
OUTPUT_POLICY = "sensevoice_control_tokens_v1"
FEATURE_DIM = 80  # OfflineRecognizer.from_sense_voice의 입력 특징 규격. Zipformer 설정과 독립.


class SenseVoiceAdapter:
    def __init__(self, model: dict, sample_rate_hz: int, root: Path):
        if sample_rate_hz != 16000:
            raise ValueError("SenseVoiceSmall batch 입력은 16000Hz여야 합니다.")
        self.model = model
        self.sample_rate_hz = sample_rate_hz
        directory = resolve_path(model["path"], root)
        self.model_files = {
            "model": resolve_path(model.get("model_file", "model.int8.onnx"), directory),
            "tokens": resolve_path(model.get("tokens", "tokens.txt"), directory),
        }
        self.recognizer = None
        self.last_result_metadata = None

    @property
    def metadata(self):
        return {
            "backend": "sherpa_onnx",
            "factory": "OfflineRecognizer.from_sense_voice",
            "supported_modes": ["batch"],
            "provider": "cpu",
            "feature_dim": FEATURE_DIM,
            "language": self.model["language"],
            "use_itn": self.model["use_itn"],
            "quantization": self.model.get("quantization", "int8"),
            "quantization_evidence": "declared_configuration_not_graph_dtype_inspection",
            "quantization_method": None,
            "quantization_method_reason": "배포된 INT8 ONNX를 사용. 변환·양자화 알고리즘은 검증하지 않았습니다.",
            "tail_padding_s": None,
            "tail_padding_reason": "not_used_by_offline_sensevoice",
            "output_policy": OUTPUT_POLICY,
            "control_token_pattern": CONTROL_TOKEN.pattern,
        }

    def load(self) -> None:
        if self.recognizer is not None:
            raise RuntimeError("이미 로드한 SenseVoice 모델입니다.")
        for key, path in self.model_files.items():
            if not path.is_file():
                raise FileNotFoundError(f"SenseVoice {key} 파일이 없습니다: {path}")
            if path.stat().st_size == 0:
                raise ValueError(f"SenseVoice {key} 파일이 비어 있습니다: {path}")
        import numpy as np
        import sherpa_onnx

        self.np = np
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(self.model_files["model"]),
            tokens=str(self.model_files["tokens"]),
            num_threads=self.model["num_threads"],
            provider="cpu",
            sample_rate=self.sample_rate_hz,
            feature_dim=FEATURE_DIM,
            language=self.model["language"],
            use_itn=self.model["use_itn"],
            decoding_method="greedy_search",
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
        if audio.sample_rate_hz != self.sample_rate_hz or not len(audio.samples):
            raise ValueError("비어 있지 않은 16000Hz 오디오가 필요합니다.")
        stream = self.recognizer.create_stream()
        stream.accept_waveform(
            self.sample_rate_hz, self.np.frombuffer(audio.samples, dtype=self.np.float32)
        )
        self.recognizer.decode_stream(stream)
        raw = stream.result.text
        if not isinstance(raw, str):
            raise TypeError("SenseVoice가 문자열 전사를 반환하지 않았습니다.")
        tags = CONTROL_TOKEN.findall(raw)
        self.last_result_metadata = {
            "raw_text": raw,
            "special_tags": tags,
            "output_policy": OUTPUT_POLICY,
        }
        return CONTROL_TOKEN.sub("", raw).strip()

    def close(self) -> None:
        self.recognizer = None
        self.last_result_metadata = None
