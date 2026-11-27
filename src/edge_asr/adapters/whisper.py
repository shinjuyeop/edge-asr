"""다국어 Whisper Base ONNX의 한국어 전사."""

from edge_asr.dataset import resolve_path


class WhisperAdapter:
    def __init__(self, model, sample_rate_hz, root):
        self.model = model
        self.sample_rate_hz = sample_rate_hz
        directory = resolve_path(model["path"], root)
        self.model_files = {
            key: resolve_path(model[key], directory) for key in ("encoder", "decoder", "tokens")
        }
        self.recognizer = None

    @property
    def metadata(self):
        return {
            "backend": "sherpa_onnx",
            "factory": "OfflineRecognizer.from_whisper",
            "provider": "cpu",
            "language": self.model["language"],
            "task": "transcribe",
            "supported_modes": ["batch"],
            "feature_policy": "sherpa_onnx_whisper",
        }

    def load(self):
        import numpy as np
        import sherpa_onnx

        for path in self.model_files.values():
            if not path.is_file():
                raise FileNotFoundError(path)
        self.np = np
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
            **{key: str(path) for key, path in self.model_files.items()},
            language=self.model["language"],
            task="transcribe",
            num_threads=self.model["num_threads"],
            provider="cpu",
        )

    def transcribe(self, audio):
        if self.recognizer is None:
            raise RuntimeError("Whisper 모델을 먼저 로드해야 합니다.")
        if audio.sample_rate_hz != self.sample_rate_hz or not audio.samples:
            raise ValueError("비어 있지 않은 16kHz PCM이 필요합니다.")
        stream = self.recognizer.create_stream()
        stream.accept_waveform(
            self.sample_rate_hz, self.np.frombuffer(audio.samples, dtype=self.np.float32)
        )
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()

    def close(self):
        self.recognizer = None
