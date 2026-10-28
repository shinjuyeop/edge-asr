"""추론 라이브러리를 불러오지 않는 최소 TOML 설정 검사."""

from pathlib import Path
import math
import tomllib

MODEL_FAMILIES = {"zipformer", "sensevoice", "qwen3_asr"}


def load_config(path: str | Path) -> dict:
    """실험 설정을 읽고 필수 항목을 검사한다. 자산 존재 여부는 검사하지 않는다."""
    with Path(path).open("rb") as handle:
        config = tomllib.load(handle)

    for section in ("model", "audio", "benchmark"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"[{section}] 테이블이 필요합니다.")

    if not isinstance(config["model"].get("family"), str) or config["model"].get("family") not in MODEL_FAMILIES:
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


def load_evaluation_config(path: str | Path) -> dict:
    """파일 평가 설정. 기존 streaming_probe용 설정과 구분한다."""
    with Path(path).open("rb") as handle:
        supplied = tomllib.load(handle)
    if set(supplied) - {"model", "audio", "evaluation"}:
        raise ValueError("파일 평가 설정은 [model], [audio], [evaluation]만 사용합니다.")
    for section in ("model", "audio", "evaluation"):
        if not isinstance(supplied.get(section), dict):
            raise ValueError(f"[{section}] 테이블이 필요합니다.")
    allowed = {
        "model": {"family", "path", "num_threads", "tokens", "encoder", "decoder", "joiner", "tail_padding_s"},
        "audio": {"sample_rate_hz"},
        "evaluation": {"mode", "warmup_runs", "normalization", "output_dir"},
    }
    for section, keys in allowed.items():
        if set(supplied[section]) - keys:
            raise ValueError(f"알 수 없는 {section} 설정: {sorted(set(supplied[section]) - keys)}")
    config = {
        "model": {"num_threads": 2, "tail_padding_s": 0.66, **supplied["model"]},
        "audio": {"sample_rate_hz": 16000, **supplied["audio"]},
        "evaluation": {"mode": "batch", "warmup_runs": 3, "normalization": "ko_cer_v1",
                       "output_dir": "results/evaluations", **supplied["evaluation"]},
    }
    if not isinstance(config["model"].get("family"), str) or config["model"].get("family") not in MODEL_FAMILIES:
        raise ValueError(f"알 수 없는 모델: {config['model'].get('family')}")
    if not isinstance(config["model"].get("path"), str) or not config["model"]["path"].strip():
        raise ValueError("model.path가 필요합니다.")
    for section, key, minimum in (("model", "num_threads", 1), ("audio", "sample_rate_hz", 1), ("evaluation", "warmup_runs", 0)):
        value = config[section][key]
        if type(value) is not int or value < minimum:
            raise ValueError(f"{section}.{key}는 {minimum} 이상의 정수여야 합니다.")
    padding = config["model"]["tail_padding_s"]
    if type(padding) not in (int, float) or not math.isfinite(padding) or not 0 <= padding <= 10:
        raise ValueError("model.tail_padding_s는 0~10초의 유한한 수여야 합니다.")
    for key in ("tokens", "encoder", "decoder", "joiner"):
        if key in config["model"] and (not isinstance(config["model"][key], str) or not config["model"][key].strip()):
            raise ValueError(f"model.{key}는 파일 경로 문자열이어야 합니다.")
    from edge_asr.metrics import NORMALIZATIONS
    if config["evaluation"]["mode"] != "batch":
        raise ValueError("공통 파일 평가의 현재 지원 모드는 batch입니다.")
    if config["evaluation"]["normalization"] not in NORMALIZATIONS:
        raise ValueError("지원하지 않는 정규화 정책입니다.")
    if not isinstance(config["evaluation"]["output_dir"], str) or not config["evaluation"]["output_dir"].strip():
        raise ValueError("evaluation.output_dir는 경로 문자열이어야 합니다.")
    return config
