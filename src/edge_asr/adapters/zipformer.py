"""sherpa-onnx Zipformer·Nemotron의 상태 유지형 Transducer 어댑터."""

from pathlib import Path

from edge_asr.dataset import Audio, resolve_path

MODEL_FILES = {
    "tokens": "tokens.txt",
    "encoder": "encoder-epoch-99-avg-1.int8.onnx",
    "decoder": "decoder-epoch-99-avg-1.onnx",
    "joiner": "joiner-epoch-99-avg-1.int8.onnx",
}


class ZipformerAdapter:
    def __init__(self, model: dict, sample_rate_hz: int, root: Path):
        self.model = model
        self.sample_rate_hz = sample_rate_hz
        directory = resolve_path(model["path"], root)
        self.model_files = {
            key: resolve_path(model.get(key, filename), directory)
            for key, filename in MODEL_FILES.items()
        }
        self.recognizer = None

    @property
    def metadata(self):
        return {
            "backend": "sherpa_onnx",
            "factory": "OnlineRecognizer.from_transducer",
            "supported_modes": ["batch", "paced_stream"],
            "provider": "cpu",
            "feature_dim": 80,
            "language": "ko",
            "language_policy": "per_stream_ko"
            if self.model.get("language")
            else "Korean_model_not_runtime_language_argument",
            "endpoint_trailing_silence_s": self.model.get("endpoint_silence_s"),
            "use_itn": None,
            "itn_reason": "no_separate_itn_in_this_adapter",
            "quantization": {
                key: "int8_filename" if ".int8." in path.name else "unspecified_by_filename"
                for key, path in self.model_files.items()
                if key != "tokens"
            },
            "quantization_evidence": "filename_convention_not_graph_dtype_inspection",
            "quantization_method": None,
            "quantization_method_reason": "배포 모델의 양자화 알고리즘은 검증하지 않았습니다.",
            "tail_padding_s": self.model["tail_padding_s"],
            "output_policy": "runtime_text_unmodified",
        }

    def load(self) -> None:
        # score·manifest 검사에는 이 무거운 선택 의존성을 import하지 않는다.
        import numpy as np
        import sherpa_onnx

        if self.model.get("language") and not hasattr(
            getattr(sherpa_onnx, "OnlineStream", None), "set_option"
        ):
            raise RuntimeError(
                "Nemotron에는 OnlineStream.set_option을 지원하는 sherpa-onnx가 필요합니다."
            )
        self.np = np
        self.padding = np.zeros(
            round(self.model["tail_padding_s"] * self.sample_rate_hz), dtype=np.float32
        )
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            **{key: str(path) for key, path in self.model_files.items()},
            num_threads=self.model["num_threads"],
            provider="cpu",
            sample_rate=self.sample_rate_hz,
            feature_dim=80,
            decoding_method="greedy_search",
            **(
                {
                    "enable_endpoint_detection": True,
                    "rule1_min_trailing_silence": self.model["endpoint_silence_s"],
                    "rule2_min_trailing_silence": self.model["endpoint_silence_s"],
                    "rule3_min_utterance_length": float("inf"),
                }
                if self.model.get("endpoint_silence_s")
                else {}
            ),
        )

    def transcribe(self, audio: Audio) -> str:
        if self.recognizer is None:
            raise RuntimeError("모델을 먼저 로드해야 합니다.")
        if audio.sample_rate_hz != self.sample_rate_hz:
            raise ValueError("어댑터와 오디오의 샘플레이트가 다릅니다.")
        stream = self.start_stream()
        self.feed(stream, audio.samples)
        return self.finish_stream(stream)

    def close(self) -> None:
        self.recognizer = None

    def start_stream(self):
        if self.recognizer is None:
            raise RuntimeError("모델을 먼저 로드해야 합니다.")
        stream = self.recognizer.create_stream()
        if language := self.model.get("language"):
            stream.set_option("language", language)
        return stream

    def _decode_result(self, stream, on_decode=None) -> str:
        while self.recognizer.is_ready(stream):
            if on_decode is None:
                self.recognizer.decode_stream(stream)
                continue
            on_decode(self.recognizer.decode_stream, stream)
        result = self.recognizer.get_result(stream)
        text = result if isinstance(result, str) else getattr(result, "text", None)
        if not isinstance(text, str):
            raise TypeError("모델이 문자열 가설을 반환하지 않았습니다.")
        return text

    def feed(self, stream, samples):
        stream.accept_waveform(
            self.sample_rate_hz, self.np.frombuffer(samples, dtype=self.np.float32)
        )

    def accept_chunk(self, stream, samples, on_decode=None) -> str:
        self.feed(stream, samples)
        return self._decode_result(stream, on_decode)

    def finish_stream(self, stream, on_decode=None) -> str:
        if len(self.padding):
            stream.accept_waveform(self.sample_rate_hz, self.padding)
        stream.input_finished()
        return self._decode_result(stream, on_decode)

    def is_endpoint(self, stream):
        return self.recognizer.is_endpoint(stream)

    def reset_endpoint(self, stream):
        self.recognizer.reset(stream)

    def token_snapshot(self, stream) -> dict:
        """선택적 진단. decode/reset 없이 설치된 native 결과를 정밀도 손실 없이 읽는다."""
        if self.recognizer is None:
            raise RuntimeError("모델을 먼저 로드해야 합니다.")
        native = getattr(self.recognizer, "recognizer", None)
        if native is None or not hasattr(native, "get_result"):
            raise TypeError("설치된 런타임의 native 토큰 결과 API를 사용할 수 없습니다.")
        result = native.get_result(stream)
        return {
            "text": result.text,
            "tokens": list(result.tokens),
            "timestamps": list(result.timestamps),
            "start_time": result.start_time,
            "segment": result.segment,
            "is_final": result.is_final,
        }
