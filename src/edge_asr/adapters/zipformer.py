"""sherpa-onnx streaming Zipformer의 파일 일괄 처리 어댑터."""

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
        self.model_files = {key: resolve_path(model.get(key, filename), directory)
                            for key, filename in MODEL_FILES.items()}
        self.recognizer = None

    def load(self) -> None:
        # score·manifest 검사에는 이 무거운 선택 의존성을 import하지 않는다.
        import numpy as np
        import sherpa_onnx

        self.np = np
        self.padding = np.zeros(round(self.model["tail_padding_s"] * self.sample_rate_hz), dtype=np.float32)
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            **{key: str(path) for key, path in self.model_files.items()},
            num_threads=self.model["num_threads"], provider="cpu",
            sample_rate=self.sample_rate_hz, feature_dim=80, decoding_method="greedy_search",
        )

    def transcribe(self, audio: Audio) -> str:
        if self.recognizer is None:
            raise RuntimeError("모델을 먼저 로드해야 합니다.")
        if audio.sample_rate_hz != self.sample_rate_hz:
            raise ValueError("어댑터와 오디오의 샘플레이트가 다릅니다.")
        stream = self.recognizer.create_stream()  # 파일마다 상태를 새로 만듦
        samples = self.np.frombuffer(audio.samples, dtype=self.np.float32)
        stream.accept_waveform(self.sample_rate_hz, samples)
        if len(self.padding):
            stream.accept_waveform(self.sample_rate_hz, self.padding)
        stream.input_finished()
        while self.recognizer.is_ready(stream):
            self.recognizer.decode_stream(stream)
        result = self.recognizer.get_result(stream)
        text = result if isinstance(result, str) else getattr(result, "text", None)
        if not isinstance(text, str):
            raise TypeError("모델이 문자열 가설을 반환하지 않았습니다.")
        return text

    def close(self) -> None:
        self.recognizer = None
