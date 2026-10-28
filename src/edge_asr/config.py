"""추론 라이브러리를 불러오지 않는 최소 TOML 설정 검사."""

from pathlib import Path
import tomllib

MODEL_FAMILIES = {"zipformer", "sensevoice", "qwen3_asr"}


def load_config(path: str | Path) -> dict:
    """실험 설정을 읽고 필수 항목을 검사한다. 자산 존재 여부는 검사하지 않는다."""
    with Path(path).open("rb") as handle:
        config = tomllib.load(handle)

    for section in ("model", "audio", "benchmark"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"[{section}] 테이블이 필요합니다.")

    if config["model"].get("family") not in MODEL_FAMILIES:
        raise ValueError(f"model.family는 {sorted(MODEL_FAMILIES)} 중 하나여야 합니다.")
    for section, key in (("model", "path"), ("audio", "path")):
        value = config[section].get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{section}.{key}에 자산 경로를 지정하세요.")

    for section, key in (
        ("model", "num_threads"),
        ("audio", "sample_rate_hz"),
        ("audio", "chunk_ms"),
        ("benchmark", "warmup_runs"),
        ("benchmark", "repetitions"),
    ):
        value = config[section].get(key)
        minimum = 0 if key == "warmup_runs" else 1
        if type(value) is not int or value < minimum:
            raise ValueError(f"{section}.{key}는 {minimum} 이상의 정수여야 합니다.")

    if config["benchmark"].get("mode") not in {"batch", "paced_stream"}:
        raise ValueError("benchmark.mode는 batch 또는 paced_stream이어야 합니다.")
    return config
