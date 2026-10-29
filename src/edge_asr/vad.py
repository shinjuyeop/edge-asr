"""검증한 Silero 파일과 공통 factory. 런타임은 호출 시에만 import한다."""

from pathlib import Path

MODEL_SHA = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"

ANNOTATED_SETTINGS = {
    "rate": 16000,
    "num_threads": 1,
    "window": 512,
    "threshold": 0.5,
    "min_speech_s": 0.1,
    "min_silence_s": 0.3,
    "hard_limit_s": 5,
    "native_soft_limit_s": 5,
    "native_buffer_s": 31,
    "native_active_budget_s": 30,
    "prefix_frames": 0,
    "suffix_frames": 0,
    "paced": False,
    "repetitions": 1,
    "warmups": 1,
}


def create_vad(model, *, soft_limit_s=5, buffer_s=31):
    import sherpa_onnx as sherpa

    config = sherpa.VadModelConfig()
    config.sample_rate = 16000
    config.num_threads = 1
    config.provider = "cpu"
    for key, value in dict(
        model=str(Path(model).resolve()),
        threshold=0.5,
        min_speech_duration=0.1,
        min_silence_duration=0.3,
        max_speech_duration=soft_limit_s,
        window_size=512,
    ).items():
        setattr(config.silero_vad, key, value)
    return sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=buffer_s)
