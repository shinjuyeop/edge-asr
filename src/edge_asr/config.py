"""추론 라이브러리를 불러오지 않는 최소 TOML 설정 검사."""

import math
import tomllib
from pathlib import Path

from edge_asr.adapters import SUPPORTED_MODES

MODEL_FAMILIES = {"zipformer", "sensevoice", "qwen3_asr"}


def load_config(path: str | Path) -> dict:
    """실험 설정을 읽고 필수 항목을 검사한다. 자산 존재 여부는 검사하지 않는다."""
    with Path(path).open("rb") as handle:
        config = tomllib.load(handle)

    for section in ("model", "audio", "benchmark"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"[{section}] 테이블이 필요합니다.")

    if (
        not isinstance(config["model"].get("family"), str)
        or config["model"].get("family") not in MODEL_FAMILIES
    ):
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
    """공통 batch·paced_stream 평가. 기존 streaming_probe용 설정과 구분한다."""
    with Path(path).open("rb") as handle:
        supplied = tomllib.load(handle)
    if set(supplied) - {"model", "audio", "evaluation", "streaming"}:
        raise ValueError(
            "공통 평가 설정은 [model], [audio], [evaluation], [streaming]을 사용합니다."
        )
    for section in ("model", "audio", "evaluation"):
        if not isinstance(supplied.get(section), dict):
            raise ValueError(f"[{section}] 테이블이 필요합니다.")
    family = supplied["model"].get("family")
    if not isinstance(family, str) or family not in SUPPORTED_MODES:
        raise ValueError(f"아직 구현하지 않은 공통 평가 모델: {family}")
    mode = supplied["evaluation"].get("mode", "batch")
    if not isinstance(mode, str) or mode not in SUPPORTED_MODES[family]:
        raise ValueError(f"{family}의 지원 평가 모드는 {SUPPORTED_MODES[family]}입니다: {mode}")
    model_keys = {"family", "path", "num_threads", "tokens"}
    model_keys |= (
        {"encoder", "decoder", "joiner", "tail_padding_s"}
        if family == "zipformer"
        else {"model_file", "language", "use_itn", "quantization"}
    )
    allowed = {
        "model": model_keys,
        "audio": {"sample_rate_hz"},
        "evaluation": {"mode", "warmup_runs", "normalization", "output_dir"},
    }
    for section, keys in allowed.items():
        if set(supplied[section]) - keys:
            raise ValueError(f"알 수 없는 {section} 설정: {sorted(set(supplied[section]) - keys)}")
    model_defaults = (
        {"num_threads": 2, "tail_padding_s": 0.66}
        if family == "zipformer"
        else {
            "num_threads": 1,
            "model_file": "model.int8.onnx",
            "tokens": "tokens.txt",
            "quantization": "int8",
        }
    )
    config = {
        "model": {**model_defaults, **supplied["model"]},
        "audio": {"sample_rate_hz": 16000, **supplied["audio"]},
        "evaluation": {
            "mode": "batch",
            "warmup_runs": 3,
            "normalization": "ko_cer_v1",
            "output_dir": "results/evaluations",
            **supplied["evaluation"],
        },
    }
    if (
        not isinstance(config["model"].get("family"), str)
        or config["model"].get("family") not in MODEL_FAMILIES
    ):
        raise ValueError(f"알 수 없는 모델: {config['model'].get('family')}")
    if not isinstance(config["model"].get("path"), str) or not config["model"]["path"].strip():
        raise ValueError("model.path가 필요합니다.")
    for section, key, minimum in (
        ("model", "num_threads", 1),
        ("audio", "sample_rate_hz", 1),
        ("evaluation", "warmup_runs", 0),
    ):
        value = config[section][key]
        if type(value) is not int or value < minimum:
            raise ValueError(f"{section}.{key}는 {minimum} 이상의 정수여야 합니다.")
    if family == "zipformer":
        padding = config["model"]["tail_padding_s"]
        if (
            type(padding) not in (int, float)
            or not math.isfinite(padding)
            or not 0 <= padding <= 10
        ):
            raise ValueError("model.tail_padding_s는 0~10초의 유한한 수여야 합니다.")
    else:
        language = config["model"].get("language")
        if not isinstance(language, str) or language not in {"ko", "auto", "zh", "en", "ja", "yue"}:
            raise ValueError("SenseVoice model.language를 명시하세요: ko/auto/zh/en/ja/yue")
        if type(config["model"].get("use_itn")) is not bool:
            raise ValueError("SenseVoice model.use_itn을 true/false로 명시하세요.")
        if config["model"]["quantization"] != "int8":
            raise ValueError("현재 SenseVoice 어댑터는 배포 INT8 모델 설정을 사용합니다.")
        if config["audio"]["sample_rate_hz"] != 16000:
            raise ValueError("SenseVoice 입력은 16000Hz여야 합니다.")
    for key in ("tokens", "encoder", "decoder", "joiner", "model_file"):
        if key in config["model"] and (
            not isinstance(config["model"][key], str) or not config["model"][key].strip()
        ):
            raise ValueError(f"model.{key}는 파일 경로 문자열이어야 합니다.")
    from edge_asr.metrics import NORMALIZATIONS

    if not isinstance(config["evaluation"]["mode"], str) or config["evaluation"]["mode"] not in {
        "batch",
        "paced_stream",
    }:
        raise ValueError("공통 평가 모드는 batch 또는 paced_stream입니다.")
    if config["evaluation"]["mode"] == "paced_stream":
        streaming = supplied.get("streaming")
        if not isinstance(streaming, dict):
            raise ValueError("paced_stream에는 [streaming] 테이블이 필요합니다.")
        if set(streaming) - {"chunk_ms", "queue_capacity_chunks", "drop_policy", "drain_timeout_s"}:
            raise ValueError("알 수 없는 streaming 설정입니다.")
        streaming = {
            "chunk_ms": 100,
            "queue_capacity_chunks": 5,
            "drop_policy": "drop_newest",
            "drain_timeout_s": 10.0,
            **streaming,
        }
        for key, low, high in (("chunk_ms", 5, 1000), ("queue_capacity_chunks", 1, 1000)):
            value = streaming[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"streaming.{key}는 {low}~{high} 정수여야 합니다.")
        timeout = streaming["drain_timeout_s"]
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 1 <= timeout <= 300
        ):
            raise ValueError("streaming.drain_timeout_s는 1~300초의 유한한 수여야 합니다.")
        if streaming["drop_policy"] != "drop_newest":
            raise ValueError("현재 큐 초과 정책은 drop_newest입니다.")
        if config["audio"]["sample_rate_hz"] * streaming["chunk_ms"] % 1000:
            raise ValueError("chunk_ms는 정수 프레임 수여야 합니다.")
        if config["model"]["family"] != "zipformer":
            raise ValueError("paced_stream의 현재 지원 모델은 zipformer입니다.")
        config["streaming"] = streaming
    elif "streaming" in supplied:
        raise ValueError("batch 모드에는 [streaming]을 지정하지 않습니다.")
    if (
        not isinstance(config["evaluation"]["normalization"], str)
        or config["evaluation"]["normalization"] not in NORMALIZATIONS
    ):
        raise ValueError("지원하지 않는 정규화 정책입니다.")
    if (
        not isinstance(config["evaluation"]["output_dir"], str)
        or not config["evaluation"]["output_dir"].strip()
    ):
        raise ValueError("evaluation.output_dir는 경로 문자열이어야 합니다.")
    return config
