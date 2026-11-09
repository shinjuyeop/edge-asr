"""Qwen 단독 반복 기준선과 고정 PCM 오류 재현. 실제 실행은 thermal-run 안에서 수행한다."""

import argparse
import os
import subprocess
import time
from copy import deepcopy
from pathlib import Path

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_event, write_json, write_jsonl
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.resources import RssSampler
from edge_asr.runtime import environment, limits_verified
from edge_asr.scoring import save_scores


def schedule(samples, repetitions, order_offset=0):
    """매 라운드 시작 파일을 회전한다. 순서가 열·초기 상태와 항상 겹치지 않게 한다."""
    for repeat in range(repetitions):
        offset = (repeat + order_offset) % len(samples)
        for sample in samples[offset:] + samples[:offset]:
            yield repeat + 1, sample


def run(
    root,
    config_path,
    manifest_path,
    output,
    *,
    protocol="baseline",
    repetitions=5,
    order_offset=0,
    adapter_factory=None,
):
    if protocol not in ("baseline", "reproduction"):
        raise ValueError("baseline 또는 reproduction 프로토콜이 필요합니다.")
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise ValueError("반복 수는 1~10 정수여야 합니다.")
    if type(order_offset) is not int or not 0 <= order_offset < 50:
        raise ValueError("순서 offset은 0~49 정수여야 합니다.")
    if protocol == "reproduction" and repetitions != 1:
        raise ValueError("재현은 manifest의 고정 순서를 한 번 실행합니다.")
    if protocol == "reproduction" and order_offset != 0:
        raise ValueError("재현 입력 순서는 바꿀 수 없습니다.")
    config = load_evaluation_config(config_path)
    if (
        config["model"]["family"] not in {"qwen3_asr", "qwen3_gguf"}
        or config["evaluation"]["mode"] != "batch"
    ):
        raise ValueError("Qwen batch 설정이 필요합니다.")
    samples, manifest_sha = load_manifest(manifest_path, root)
    if not samples:
        raise ValueError("빈 manifest입니다.")
    if protocol == "baseline" and any(not s.reference.strip() for s in samples):
        raise ValueError("기준선에는 모든 정답이 필요합니다.")
    if protocol == "reproduction" and any(s.reference for s in samples):
        raise ValueError("재현용 잘린 구간은 미주석 입력으로만 검사합니다.")
    limits = Path("/proc/self/limits").read_text()
    if not limits_verified(limits):
        raise RuntimeError("thermal-run의 4GiB 주소 공간 제한이 필요합니다.")
    output.mkdir(parents=True, exist_ok=True)
    worker = output / "worker.json"
    # 이미 실행한 경로와 다른 종류의 산출물이 섞이는 것도 거부한다.
    for name in ("worker.json", "predictions.jsonl", "measurements.jsonl", "summary.json"):
        if (output / name).exists():
            raise FileExistsError(f"기존 결과를 덮어쓰지 않습니다: {output / name}")
    info = {
        "schema_version": 1,
        "kind": "qwen3_opt0_" + protocol,
        "status": "preparing",
        "process_id": os.getpid(),
        "limits": limits,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip()
        ),
        "config": config,
        "config_sha256": digest(config_path),
        "input_manifest_sha256": manifest_sha,
        "environment": environment(),
        "repetitions": repetitions,
        "order_offset": order_offset,
        "model_load": None,
        "calls": [],
        "unmeasured": {"ttft_s": None, "encoder_s": None, "prefill_s": None, "decode_s": None},
        "timing_scope": "준비한 float PCM에서 adapter.transcribe 전체. WAV I/O/해시/변환/기록 제외. 스트림 생성·특징 추출·encoder·prefill·decode·출력 검사 포함.",
        "load_scope": "adapter.load의 런타임 import·인식기 초기화. 자산을 먼저 해시하므로 cold-storage 측정 아님.",
        "resource_scope": "100ms/경계 주 PID RSS 관측. 호출 중 계측 스레드 CPU 포함. RSS 실제 피크 보장 없음.",
    }
    write_json(worker, info)
    rows, adapter = [], None
    code = 1
    try:
        # 준비와 자산 검사 비용은 로딩/추론 타이머 밖에 둔다.
        audio = {s.id: load_audio(s.audio_path, 16000) for s in samples}
        for s in samples:
            if s.audio_sha256 is None or s.audio_sha256 != audio[s.id].sha256:
                raise ValueError(f"입력 SHA-256 불일치 또는 누락: {s.id}")
        for repeat, s in schedule(samples, repetitions, order_offset):
            rows.append(
                {
                    "schema_version": 1,
                    "mode": "batch",
                    "id": f"{s.id}-r{repeat:02d}",
                    "source_id": s.id,
                    "repeat": repeat,
                    "reference": s.reference,
                    "audio_path": str(s.audio_path),
                    "split": s.split,
                    "condition": s.condition,
                    "status": "not_attempted",
                    "hypothesis": None,
                    "audio": audio[s.id].metadata(),
                    "elapsed_s": None,
                    "process_cpu_s": None,
                    "resources": None,
                    "error": None,
                }
            )
        write_jsonl(output / "predictions.jsonl", rows)
        adapter = (adapter_factory or create_adapter)(config["model"], 16000, root)
        info["model_files"] = {
            name: {"path": str(p), "sha256": digest(p)} for name, p in adapter.model_files.items()
        }
        info["model_metadata"] = deepcopy(adapter.metadata)

        # 로딩 실패에도 RSS 관측을 보존한다.
        info["status"] = "model_load"
        write_json(worker, info)
        monitor = RssSampler(sample_device=True)
        try:
            with monitor:
                began, cpu = time.perf_counter(), time.process_time()
                try:
                    adapter.load()
                finally:
                    info["model_load"] = {
                        "elapsed_s": time.perf_counter() - began,
                        "process_cpu_s": time.process_time() - cpu,
                    }
        finally:
            if info["model_load"] is not None:
                info["model_load"]["resources"] = monitor.metadata()
            info["environment"] = environment()
            write_json(worker, info)

        with (output / "measurements.jsonl").open("x", encoding="utf-8") as log:

            def transcribe(s, phase, repeat=0):
                result = {
                    "source_id": s.id,
                    "phase": phase,
                    "repeat": repeat,
                    "status": "failed",
                    "hypothesis": None,
                    "error": None,
                }
                info["status"] = phase
                info["active_call"] = {"source_id": s.id, "phase": phase, "repeat": repeat}
                write_json(worker, info)
                prepare_call = getattr(adapter, "prepare_call", None)
                if prepare_call is not None:
                    prepare_call(phase, s.id, repeat)
                monitor = RssSampler(sample_device=True)
                try:
                    with monitor:
                        began, cpu = time.perf_counter(), time.process_time()
                        try:
                            text = adapter.transcribe(audio[s.id])
                            if not isinstance(text, str):
                                raise TypeError("ASR 전사는 문자열이어야 합니다.")
                            result.update(status="success", hypothesis=text)
                        finally:
                            result.update(
                                elapsed_s=time.perf_counter() - began,
                                process_cpu_s=time.process_time() - cpu,
                            )
                except Exception as error:
                    result["error"] = {"type": type(error).__name__, "message": str(error)}
                result.update(
                    resources=monitor.metadata(),
                    adapter_result=deepcopy(adapter.last_result_metadata),
                )
                write_event(log, result)
                info["calls"].append(
                    {k: result[k] for k in ("source_id", "phase", "repeat", "status")}
                )
                info["active_call"] = None
                write_json(worker, info)
                print(f"{phase}: {s.id} {result['status']}", flush=True)
                return result

            if protocol == "baseline":
                for phase in ["first_inference", *["warmup"] * config["evaluation"]["warmup_runs"]]:
                    result = transcribe(samples[0], phase)
                    if result["status"] != "success":
                        raise RuntimeError("첫 추론/워밍업 실패로 반복 기준선을 시작하지 않습니다.")
            for row, (repeat, s) in zip(rows, schedule(samples, repetitions, order_offset)):
                # 외부 중단으로 결과가 돌아오지 않은 호출을 미실행과 구분한다.
                row["status"] = "interrupted"
                write_jsonl(output / "predictions.jsonl", rows)
                result = transcribe(s, "steady" if protocol == "baseline" else "diagnostic", repeat)
                row.update(
                    {
                        k: result[k]
                        for k in (
                            "status",
                            "hypothesis",
                            "elapsed_s",
                            "process_cpu_s",
                            "resources",
                            "error",
                            "adapter_result",
                        )
                    }
                )
                write_jsonl(output / "predictions.jsonl", rows)
        info["status"] = (
            "completed"
            if all(r["status"] == "success" for r in rows)
            else "completed_with_model_failures"
        )
        code = 0 if protocol == "reproduction" or info["status"] == "completed" else 1
    except Exception as error:
        info.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        if adapter is not None:
            adapter.close()
        if rows:
            write_jsonl(output / "predictions.jsonl", rows)
            # 미주석 재현 구간은 공통 scorer에서도 CER=null. 원 발화 정답을 재사용하지 않는다.
            save_scores(output, rows, config["evaluation"]["normalization"])
        write_json(worker, info)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", choices=("baseline", "reproduction"), default="baseline")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--order-offset", type=int, default=0)
    args = parser.parse_args()
    return run(
        Path.cwd(),
        args.config.resolve(),
        args.manifest.resolve(),
        args.output.resolve(),
        protocol=args.protocol,
        repetitions=args.repetitions,
        order_offset=args.order_offset,
    )


if __name__ == "__main__":
    raise SystemExit(main())
