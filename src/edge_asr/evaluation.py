"""모델을 한 번 로드하는 공통 파일 실행기와 추론 없는 재채점."""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_json
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest, resolve_path
from edge_asr.resources import RssSampler
from edge_asr.runtime import environment
from edge_asr.scoring import read_predictions, save_scores, write_jsonl


def new_output(base: Path, prefix: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    return Path(tempfile.mkdtemp(prefix=prefix + stamp, dir=base)).resolve()


def evaluate(
    config_path: Path,
    manifest_path: Path,
    root: Path,
    *,
    output_base: Path | None = None,
    adapter_factory=None,
) -> tuple[Path, int]:
    config = load_evaluation_config(config_path)
    mode = config["evaluation"]["mode"]
    if mode == "paced_stream":
        from edge_asr.streaming import run_paced, warmup_stream
    samples, manifest_hash = load_manifest(manifest_path, root)
    output = new_output(
        output_base or resolve_path(config["evaluation"]["output_dir"], root), "eval-"
    )
    print(f"결과 폴더: {output}", flush=True)
    rows = [
        {
            "schema_version": 1,
            "mode": mode,
            "id": sample.id,
            "audio_path": str(sample.audio_path),
            "reference": sample.reference,
            "expected_audio_sha256": sample.audio_sha256,
            "split": sample.split,
            "condition": sample.condition,
            "status": "not_attempted",
            "hypothesis": None,
            "audio": None,
            "elapsed_s": None,
            "process_cpu_s": None,
            "resources": None,
            "error": None,
        }
        for sample in samples
    ]
    if mode == "paced_stream":
        for row in rows:
            row["streaming"] = None
    info = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "process_id": os.getpid(),
        "root": str(root),
        "output_directory": str(output),
        "mode": mode,
        "config": config,
        "file_execution": "sequential",
        "batch_size": 1,
        "repetitions_per_sample": 1,
        "input_manifest": {
            "path": str(manifest_path),
            "sha256": manifest_hash,
            "samples": len(samples),
        },
        "config_sha256": digest(config_path),
        "source_commit": None,
        "source_dirty": None,
        "model_load_s": None,
        "model_load_cpu_s": None,
        "model_load_resources": None,
        "model_load_scope": "adapter.load의 선택 런타임 import·인식기 초기화. 자산 해시를 먼저 읽으므로 cold-storage 측정이 아님.",
        "model_metadata": {
            "family": config["model"]["family"],
            "configuration": deepcopy(config["model"]),
        },
        "model_files": {},
        "warmup": {"planned": config["evaluation"]["warmup_runs"], "successful": 0},
        "timing_scope": "준비한 float PCM에서 새 인식 상태 생성~최종 반환. WAV I/O·변환·모델 로딩·워밍업·결과 파일 쓰기 제외. 특징 추출·모델별 종료 처리(해당 시 flush)·출력 정리 포함.",
        "resource_scope": "추론 구간의 현재 프로세스 CPU(계측 스레드 비용 포함), 모델 상주 상태의 주 PID 표본 RSS. RSS는 경계 표본 포함·정확한 피크 아님.",
    }
    info["unmeasured_reasons"] = {
        "aligned_recognition_latency_s": "정답의 음향 시각 정렬이 없습니다.",
        "offline_refine_confirmation_latency_s": "live 구간 수집·endpoint·refine 큐를 실행하지 않았습니다.",
        "acoustic_endpoint_latency_s": "음향적 발화 끝 검출을 실행하지 않았습니다.",
        "ui_display_latency_s": "자막 화면을 계측하지 않았습니다.",
    }
    if mode == "batch":
        info["unmeasured_reasons"].update(
            first_partial_from_audio_start_s="batch는 부분 자막 시각을 계측하지 않습니다.",
            finalization_from_audio_end_s="batch 처리 시간은 실시간 입력 끝 기준 지연이 아닙니다.",
            drops="실시간 입력 큐를 실행하지 않았습니다.",
            backlog="실시간 입력 큐를 실행하지 않았습니다.",
        )
    if mode == "paced_stream":
        info.update(
            capture_process_start_method="spawn",
            playback_speed=1.0,
            timing_scope="elapsed_s/RTF는 스트림 생성·각 청크 전달/디코딩/가설 반환·flush의 활성 wall 합. 입력 대기·IPC·기록·spawn·워밍업 제외. replay_elapsed_s는 t0~최종 반환.",
            resource_scope="process_cpu_s는 활성 호출 구간의 주 PID CPU. replay_process_cpu_s는 1배속 대기 포함 주 PID CPU. RSS·온도·클럭은 100ms/호출 경계 관측. 입력 자식 CPU·시작 포함 RSS 최고치는 별도.",
        )
        info["warmup"]["mode"] = "unpaced_chunk_feed"
    package_root = Path(__file__).resolve().parent
    source_root = package_root.parents[1]
    info["source_sha256"] = {
        str(p.relative_to(package_root)): digest(p) for p in package_root.rglob("*.py")
    }
    if (source_root / ".git").exists():
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source_root, capture_output=True, text=True
            )
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=source_root, capture_output=True, text=True
            )
            info["source_commit"] = head.stdout.strip() if head.returncode == 0 else None
            info["source_dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
        except OSError:
            pass
    write_json(output / "manifest.json", info)
    write_json(output / "environment.json", environment())
    # 정답이 포함된 raw 결과는 로컬 전용. 원본 manifest를 수정하지 않는다.
    write_jsonl(output / "predictions.jsonl", rows)
    adapter, current = None, None
    warmed, code = False, 0
    try:
        adapter = (adapter_factory or create_adapter)(
            config["model"], config["audio"]["sample_rate_hz"], root
        )
        info["model_metadata"]["adapter"] = deepcopy(getattr(adapter, "metadata", None))
        info["model_files"] = {
            key: {"path": str(path), "sha256": digest(path)}
            for key, path in adapter.model_files.items()
        }
        write_json(output / "manifest.json", info)
        loading = RssSampler()
        try:
            with loading:
                started, cpu_started = time.perf_counter(), time.process_time()
                try:
                    adapter.load()
                finally:
                    info["model_load_s"] = time.perf_counter() - started
                    info["model_load_cpu_s"] = time.process_time() - cpu_started
        finally:
            info["model_load_resources"] = loading.metadata()
            write_json(output / "environment.json", environment())
            write_json(output / "manifest.json", info)
        with (output / "progress.jsonl").open("x", encoding="utf-8") as progress:
            for ordinal, (sample, row) in enumerate(zip(samples, rows), 1):
                current = row
                stage = "audio"
                audio = None
                try:
                    audio = load_audio(sample.audio_path, config["audio"]["sample_rate_hz"])
                    row["audio"] = audio.metadata()
                    if sample.audio_sha256 is not None and sample.audio_sha256 != audio.sha256:
                        raise ValueError("manifest의 audio_sha256와 현재 WAV가 다릅니다.")
                    if not warmed:
                        stage = "warmup"
                        info["warmup"]["sample_id"] = sample.id
                        for _ in range(config["evaluation"]["warmup_runs"]):
                            hypothesis = (
                                adapter.transcribe(audio)
                                if mode == "batch"
                                else warmup_stream(adapter, audio, config["streaming"])
                            )
                            if not isinstance(hypothesis, str):
                                raise TypeError("어댑터가 문자열을 반환해야 합니다.")
                            info["warmup"]["successful"] += 1
                            write_json(output / "manifest.json", info)
                        warmed = True
                    stage = "inference"
                    monitor = RssSampler(sample_device=mode == "paced_stream")
                    try:
                        with monitor:
                            if mode == "batch":
                                cpu_start = time.process_time()
                                wall_start = time.perf_counter()
                                try:
                                    hypothesis = adapter.transcribe(audio)
                                finally:
                                    row["elapsed_s"] = time.perf_counter() - wall_start
                                    row["process_cpu_s"] = time.process_time() - cpu_start
                            else:
                                row["streaming"] = {"events_directory": f"events/{ordinal:05d}"}
                                try:
                                    hypothesis = run_paced(
                                        adapter,
                                        audio,
                                        config["streaming"],
                                        output / row["streaming"]["events_directory"],
                                        row["streaming"],
                                    )
                                finally:
                                    row["elapsed_s"] = row["streaming"].get("processing_s")
                                    row["process_cpu_s"] = row["streaming"].get("processing_cpu_s")
                    finally:
                        row["resources"] = monitor.metadata()
                    if not isinstance(hypothesis, str):
                        raise TypeError("어댑터가 문자열을 반환해야 합니다.")
                    row["hypothesis"], row["status"] = hypothesis, "success"
                    metadata = getattr(adapter, "last_result_metadata", None)
                    if metadata is not None:
                        row["adapter_result"] = deepcopy(metadata)
                except Exception as error:
                    row["status"], code = "failed", 1
                    row["error"] = {
                        "stage": stage,
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                    if stage == "warmup":
                        info["error"] = row["error"]
                progress.write(json.dumps(row, ensure_ascii=False) + "\n")
                progress.flush()
                print(f"{sample.id}: {row['status']}", flush=True)
                if row["status"] == "failed" and stage == "warmup":
                    break
                current = None
                del audio
        info["status"] = "completed" if code == 0 else "completed_with_failures"
    except KeyboardInterrupt:
        info["status"], code = "interrupted", 130
        if current is not None and current["status"] == "not_attempted":
            current["status"] = "interrupted"
            current["error"] = {"stage": "interrupted", "message": "사용자 중단"}
    except Exception as error:
        info["status"], code = "setup_failed", 2
        info["error"] = {"type": type(error).__name__, "message": str(error)}
        (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception as error:
                info["cleanup_error"] = str(error)
                code = 2
                info["status"] = "cleanup_failed"
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_jsonl(output / "predictions.jsonl", rows)
        # 모델은 더 이상 필요 없다. 동일 scorer를 평가 직후와 재채점에서 사용.
        save_scores(output, rows, config["evaluation"]["normalization"])
        write_json(output / "manifest.json", info)
    print(f"요약: {output / 'summary.md'}", flush=True)
    return output, code


def rescore(
    predictions: Path,
    root: Path,
    *,
    normalization="ko_cer_v1",
    reference_manifest: Path | None = None,
    output_base: Path | None = None,
) -> tuple[Path, int]:
    rows = read_predictions(predictions)
    references, reference_hash = None, None
    if reference_manifest is not None:
        samples, reference_hash = load_manifest(reference_manifest, root)
        references = {sample.id: sample.reference for sample in samples}
    # 정답 ID·정규화 정책 오류는 결과 폴더를 만들기 전에 검사한다.
    from edge_asr.metrics import normalize

    normalize("", normalization)
    if references is not None and set(references) != {row["id"] for row in rows}:
        raise ValueError("교체 정답 목록과 인식 결과의 id 집합이 같아야 합니다.")
    output = new_output(output_base or root / "results/scores", "score-")
    summary = save_scores(output, rows, normalization, references)
    write_json(
        output / "provenance.json",
        {
            "schema_version": 1,
            "kind": "rescore_only",
            "predictions": str(predictions),
            "predictions_sha256": digest(predictions),
            "reference_manifest": str(reference_manifest) if reference_manifest else None,
            "reference_manifest_sha256": reference_hash,
            "normalization": normalization,
            "scorer_python": sys.version,
            "unicode_database": summary["unicode_database"],
            "scorer_source_sha256": {
                name: digest(Path(__file__).parent / name)
                for name in (
                    ("metrics.py", "scoring.py", "streaming.py")
                    if summary["mode"] == "paced_stream"
                    else ("metrics.py", "scoring.py")
                )
            },
            "note": "재추론 없이 저장 가설과 기록된 처리 시간을 사용. 바뀐 환경에서 속도를 다시 측정한 결과가 아님.",
        },
    )
    print(f"재채점 요약: {output / 'summary.md'}", flush=True)
    return output, 0 if summary["overall"]["complete"] else 1
