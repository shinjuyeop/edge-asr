"""구체적인 모델 호출만 담당한다. 공통 실행기는 이 모듈의 경계를 사용한다."""

from pathlib import Path

SUPPORTED_MODES = {"zipformer": ("batch", "paced_stream"), "sensevoice": ("batch",)}


def create_adapter(model: dict, sample_rate_hz: int, root: Path):
    if model["family"] == "zipformer":
        from edge_asr.adapters.zipformer import ZipformerAdapter

        return ZipformerAdapter(model, sample_rate_hz, root)
    if model["family"] == "sensevoice":
        from edge_asr.adapters.sensevoice import SenseVoiceAdapter

        return SenseVoiceAdapter(model, sample_rate_hz, root)
    raise ValueError(f"아직 구현하지 않은 모델 어댑터: {model['family']}")
