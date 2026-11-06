"""사전 고정한 새 문장 평가와 30분 단일 스트림 운영 검사를 순차 실행한다."""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.metrics import normalize
from edge_asr.tools.probes.qwen3_realtime import queue_loss_only, verify_offline_geometry
from edge_asr.tools.probes.thermal import run

DATASETS = {
    "prospective": "data/evaluation/fleurs-prospective30-20261106-01/manifest.jsonl",
    "sustained": "data/evaluation/sustained-30m-20261106-01/manifest.jsonl",
}
WARMUP = "models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/test_wavs/0.wav"
CONFIGS = {"sensevoice": "configs/sensevoice_eval.toml", "qwen3_asr": "configs/qwen3_eval.toml"}


def plan_cases():
    return [
        {
            "id": f"{phase}-{dataset}-{family}-t2-01",
            "phase": phase,
            "dataset": dataset,
            "family": family,
            "num_threads": 2,
            "timeout_s": 2100 if dataset == "sustained" else 900,
        }
        for phase, dataset, families in (
            ("offline", "prospective", ("sensevoice", "qwen3_asr")),
            ("concurrent", "prospective", ("qwen3_asr", "sensevoice")),
            ("concurrent", "sustained", ("sensevoice", "qwen3_asr")),
        )
        for family in families
    ]


def validate_inputs(root):
    metadata = {}
    for name, relative in DATASETS.items():
        samples, sha = load_manifest(root / relative, root)
        audio = [load_audio(s.audio_path, 16000) for s in samples]
        if any(a.sha256 != s.audio_sha256 for a, s in zip(audio, samples)):
            raise ValueError("입력 WAV 해시 불일치")
        metadata[name] = {
            "manifest": relative,
            "manifest_sha256": sha,
            "inputs": [{"id": s.id, **a.metadata()} for s, a in zip(samples, audio)],
            "total_audio_s": sum(a.duration_s for a in audio),
        }
    new = read_jsonl(root / DATASETS["prospective"])
    old = read_jsonl(root / "data/evaluation/fleurs-ko-test100/manifest.jsonl")
    old += read_jsonl(root / "data/evaluation/kss-dev50/manifest.jsonl")
    if (
        len(new) != 30
        or any(r["split"] != "prospective_evaluation" for r in new)
        or len({r["sentence_id"] for r in new}) != 30
        or len({normalize(r["reference"]) for r in new}) != 30
        or {r["sentence_id"] for r in new} & {r["sentence_id"] for r in old if "sentence_id" in r}
        or {normalize(r["reference"]) for r in new} & {normalize(r["reference"]) for r in old}
        or {r["audio_sha256"] for r in new} & {r["audio_sha256"] for r in old}
    ):
        raise ValueError("새 문장30개와 기존 자료의 분리 조건 불일치")
    if not 0 < metadata["prospective"]["total_audio_s"] <= 600:
        raise ValueError("새 문장 합계가 사전 정한 실행 예산 범위를 벗어났습니다.")
    sustained = metadata["sustained"]
    if len(sustained["inputs"]) != 1 or not 1800 <= sustained["total_audio_s"] <= 1860:
        raise ValueError("30분 단일 원본의 연속 입력이 필요합니다.")
    warm = load_audio(root / WARMUP, 16000)
    if warm.duration_s > 5 or warm.sha256 in {r["audio_sha256"] for r in new}:
        raise ValueError("평가 입력과 분리한 최대5초 워밍업이 필요합니다.")
    return metadata, {"path": WARMUP, **warm.metadata()}


def campaign(root, output):
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip():
        raise ValueError("Host 테스트·main push 후 RP5 clean main에서 실행하세요.")
    if output.exists():
        raise FileExistsError("기존 캠페인을 덮어쓰지 않습니다.")
    inputs, warm = validate_inputs(root)
    output.mkdir(parents=True, exist_ok=False)
    (output / "configs").mkdir()
    configs = {}
    for family, relative in CONFIGS.items():
        text, n = re.subn(
            r"(?m)^num_threads = \d+$", "num_threads = 2", (root / relative).read_text()
        )
        if n != 1:
            raise ValueError("단일 스레드 설정이 필요합니다.")
        path = output / "configs" / f"{family}.toml"
        path.write_text(text)
        checked = load_evaluation_config(path)
        if (
            checked["evaluation"]["normalization"] != "ko_cer_v1"
            or checked["evaluation"]["warmup_runs"] != 3
        ):
            raise ValueError("기존 채점/워밍업 계약이 필요합니다.")
        model = checked["model"]
        if model["family"] != family or model["language"] != "ko":
            raise ValueError("기존 한국어 모델 구성이 필요합니다.")
        if family == "qwen3_asr" and (model["max_total_len"], model["max_new_tokens"]) != (
            512,
            128,
        ):
            raise ValueError("Qwen 입력/출력 예산 512/128을 유지합니다.")
        if family == "sensevoice" and model["use_itn"]:
            raise ValueError("SenseVoice ITN은 기존처럼 비활성화합니다.")
        configs[family] = {"path": str(path), "sha256": digest(path)}
    cases = plan_cases()
    plan = {
        "schema": "prospective-and-sustained-v1",
        "cases": cases,
        "datasets": inputs,
        "warmup": warm,
        "configs": configs,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "zip_threads": 1,
            "refine_threads": 2,
            "vad_threads": 1,
            "policy": "vad0",
            "maximum_owned_s": 5,
            "context_frames": 0,
            "capture_chunk_ms": 100,
            "capture_queue_chunks": 5,
            "refine_queue_windows": 2,
            "warmup_runs": 3,
            "automatic_replacements": 0,
        },
        "scope": "새 문장30개는 첫 평가 후 재튜닝 금지. 화자 독립/학습 비중복 미확인. 지속 검사는 합성 반복 단일 입력 각30분1회; 마이크/상시 안정성 아님",
        "failure_policy": "입력/추론/계측/제한 실패 중단. 검증된 Refine 큐 포화만 있으면 실패를 보존하고 다음 고정 조건 진행",
        "sustained_analysis": {
            "time_origin": "replay_start_ns",
            "first_window_s": [60, 360],
            "last_window_s": ["audio_s-360", "audio_s-60"],
            "bins_s": 300,
            "rss_comparison": "동시 관측한 주 PID+Refine PID RSS 합의 창별 중앙값. 반복 안정성/누수 부재 입증 아님",
        },
    }
    write_json(output / "plan.json", plan)
    status = {"status": "running", "cases": []}
    losses = False
    verified = False
    write_json(output / "campaign.json", status)
    try:
        for case in cases:
            if case["phase"] == "concurrent" and not verified:
                write_json(output / "offline-geometry.json", verify_offline_geometry(output, cases))
                verified = True
            directory = output / case["id"]
            command = [sys.executable, "-u", "scripts/edge_asr.py", "tools"]
            if case["phase"] == "offline":
                command += [
                    "probe-segmented-accuracy",
                    "--config",
                    configs[case["family"]]["path"],
                    "--condition",
                    "bounded_vad_5s",
                ]
            else:
                command += [
                    "probe-concurrent",
                    "--zip-threads",
                    "1",
                    "--refine-threads",
                    "2",
                    "--refine-policy",
                    "vad0",
                    "--refine-config",
                    configs[case["family"]]["path"],
                ]
            command += [
                "--manifest",
                str(root / DATASETS[case["dataset"]]),
                "--output",
                str(directory),
                "--warmup-wav",
                str(root / WARMUP),
            ]
            code = run(
                root,
                directory,
                command,
                temperature_policy="operational",
                timeout_s=case["timeout_s"],
            )
            loss = bool(code and case["phase"] == "concurrent" and queue_loss_only(directory))
            status["cases"].append({"id": case["id"], "exit_code": code, "queue_loss_only": loss})
            if code and not loss:
                status["status"] = "stopped_after_failure"
                write_json(output / "campaign.json", status)
                return 1
            losses |= loss
            write_json(output / "campaign.json", status)
        status["status"] = "completed_with_losses" if losses else "completed"
    except BaseException as error:
        status.update(status="failed", error=repr(error))
        write_json(output / "campaign.json", status)
        raise
    finally:
        status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "campaign.json", status)
    return int(losses)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return campaign(Path.cwd(), args.output.resolve())
