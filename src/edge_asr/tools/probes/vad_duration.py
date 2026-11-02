"""동일 파생 WAV로 3/5/8초 구간·30초 초과 활성·RSS를 점검한다. 정확도 평가가 아니다."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from edge_asr.artifacts import digest, write_json
from edge_asr.audio_policy import preprocess
from edge_asr.dataset import load_audio, pcm_bytes
from edge_asr.resources import RssSampler, process_sample
from edge_asr.runtime import runtime_info
from edge_asr.segmentation import SegmentPipeline
from edge_asr.vad import MODEL_SHA, create_vad


def measure(vad, audio, policy, maximum, *, bounded, convert=list):
    # 참조 PCM 생성은 호출 구간 밖이다. VAD에 공급하는 입력은 공통 전처리기를 다시 거친다.
    expected, _ = preprocess(audio.samples, policy)
    expected_sha = hashlib.sha256(pcm_bytes(expected)).hexdigest()
    events, trace = [], []
    owned = 0

    def emit(event, pcm):
        nonlocal owned
        a, b = event["owned_start_frame"], event["owned_end_frame"]
        if a < owned or b - a > maximum * 16000:
            raise ValueError("소유 구간 겹침/상한 오류")
        if pcm != expected[event["context_start_frame"] : b]:
            raise ValueError("소유/문맥 PCM 바이트 불일치")
        owned = b
        events.append(event)

    pipeline = SegmentPipeline(
        vad,
        policy,
        maximum,
        emit,
        convert=convert,
        native_budget_frames=None if bounded else 30 * 16000,
    )
    result = {"status": "failed", "error": None}
    began, cpu = time.perf_counter(), time.process_time()
    with RssSampler() as resources:
        try:
            for offset in range(0, len(audio.samples), 1600):
                pipeline.accept(
                    audio.samples[offset : offset + 1600],
                    final=offset + 1600 >= len(audio.samples),
                )
                if (offset // 1600) % 10 == 0:
                    trace.append(
                        {
                            "accepted_input_frames": pipeline.ring.end,
                            **(process_sample(os.getpid()) or {}),
                        }
                    )
            pipeline.finish()
            if pipeline.metadata()[
                "processed_pcm_sha256"
            ] != expected_sha or pipeline.ring.end != len(expected):
                raise ValueError("전체 공통 PCM/프레임 불일치")
            result["status"] = "completed"
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
    result.update(
        call_wall_s=time.perf_counter() - began,
        process_cpu_s=time.process_time() - cpu,
        resources=resources.metadata(),
        rss_by_audio_position=trace,
        pipeline=pipeline.metadata(),
        segments=events,
        over_30s_active_observed=pipeline.native_active_peak > 30 * 16000,
        native_guard_observed=bool(
            result["error"] and "native_active_budget_exhausted" in result["error"]
        ),
        expected_processed_pcm_sha256=expected_sha,
    )
    return result


def probe(root, model, manifest, output, backend):
    if backend not in ("native", "bounded"):
        raise ValueError("지원하지 않는 VAD 백엔드")
    spec = json.loads(manifest.read_text())
    if spec.get("schema") != "vad-duration-input-v1" or digest(model) != MODEL_SHA:
        raise ValueError("고정한 구간 검증 입력과 기존 가중치가 필요합니다.")
    wav = (root / spec["audio_path"]).resolve()
    if not wav.is_relative_to(root.resolve() / "data") or digest(wav) != spec["audio_sha256"]:
        raise ValueError("data/ 아래 고정 WAV 해시가 필요합니다.")
    audio = load_audio(wav, 16000)
    if len(audio.samples) != spec["frames"] or not 30 * 16000 < spec["frames"] <= 90 * 16000:
        raise ValueError("30초 초과, 90초 이하 입력이 필요합니다.")
    if (output / "worker.json").exists():
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    info = {
        "status": "running",
        "process_id": os.getpid(),
        "backend": backend,
        "manifest_sha256": digest(manifest),
        "model_sha256": MODEL_SHA,
        "audio_sha256": audio.sha256,
        "input": spec,
        "planned": 6,
        "warmups": 0,
        "repetitions": 1,
        "order": [[p, m] for p in ("identity", "rms26") for m in (3, 5, 8)],
        "scope": "비정상 반복 파생 입력의 기계적 검사. CER/실음성 정답/마이크/ASR 지연 없음. RSS 위치 표본 비용 포함.",
    }
    write_json(output / "worker.json", info)
    try:
        began, cpu = time.perf_counter(), time.process_time()
        with RssSampler() as loading:
            import numpy as np

            if backend == "bounded":
                from edge_asr.vad_probability import BoundedProbabilityVad, K2SileroProbability

                probability = K2SileroProbability(model)
                vad = BoundedProbabilityVad(probability)
                info["probability_runtime"] = probability.metadata()
            else:
                vad = create_vad(model, soft_limit_s=5, buffer_s=31)
        info["loading"] = {
            "wall_s": time.perf_counter() - began,
            "process_cpu_s": time.process_time() - cpu,
            "resources": loading.metadata(),
        }
        info["runtime"] = runtime_info()
        write_json(output / "worker.json", info)
        failures = 0
        with (output / "samples.jsonl").open("x", encoding="utf-8") as log:
            for policy, maximum in info["order"]:
                resets = probability.reset_count if backend == "bounded" else None
                result = measure(
                    vad,
                    audio,
                    policy,
                    maximum,
                    bounded=backend == "bounded",
                    convert=lambda b: np.asarray(b, dtype=np.float32),
                )
                result.update(policy=policy, hard_limit_s=maximum)
                if (
                    result["expected_processed_pcm_sha256"]
                    != spec["expected_processed"][policy]["pcm_sha256"]
                ):
                    raise ValueError("Host에서 고정한 전처리 PCM과 불일치")
                if backend == "bounded":
                    result.update(
                        bounded_vad=vad.metadata(),
                        probability_runtime=probability.metadata(),
                        resets_during_session=probability.reset_count - resets,
                    )
                failures += result["status"] != "completed"
                log.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
                log.flush()
        info.update(
            status="completed_with_failures" if failures else "completed", failed_samples=failures
        )
    except BaseException as error:
        info.update(status="failed", error=repr(error))
        raise
    finally:
        write_json(output / "worker.json", info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", type=Path, default=Path("models/silero-vad-k2-20261029/silero_vad.onnx")
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--backend", choices=("native", "bounded"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe(Path.cwd(), args.model, args.manifest, args.output, args.backend)


if __name__ == "__main__":
    raise SystemExit(main())
