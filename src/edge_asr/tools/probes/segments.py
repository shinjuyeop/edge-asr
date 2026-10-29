"""공통 전처리/VAD/강제 구간 상한의 RP5 검사. ASR·텍스트 병합은 실행하지 않는다."""

import argparse
import json
import os
import time
from array import array
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.artifacts import write_json as save
from edge_asr.dataset import Audio, load_audio, load_manifest
from edge_asr.resources import RssSampler
from edge_asr.runtime import runtime_info
from edge_asr.segmentation import SegmentPipeline, SegmentReplayAdapter, validate_capture
from edge_asr.streaming import run_paced
from edge_asr.vad import MODEL_SHA, create_vad
from edge_asr.vad_probe import source_metrics

SETTINGS = {
    "chunk_ms": 100,
    "queue_capacity_chunks": 4,
    "drop_policy": "drop_newest",
    "drain_timeout_s": 5.0,
}


def probe(root, model, manifest, output, policy, *, paced=False, maxima=(3, 5, 8)):
    if (output / "worker.json").exists():
        raise ValueError("기존 결과를 덮어쓰지 않습니다.")
    if digest(model) != MODEL_SHA:
        raise ValueError("검증한 k2-fsa Silero 16kHz 파일이 필요합니다.")
    if policy not in ("identity", "rms26") or not maxima or any(m not in (3, 5, 8) for m in maxima):
        raise ValueError("사전 정의한 정책/상한이 필요합니다.")
    samples, manifest_hash = load_manifest(manifest, root)
    expected = {r["id"]: r for r in (json.loads(l) for l in manifest.read_text().splitlines())}
    info = {
        "process_id": os.getpid(),
        "status": "running",
        "kind": "segment_pipeline",
        "model_sha256": MODEL_SHA,
        "input_manifest_sha256": manifest_hash,
        "settings": {
            "policy": policy,
            "paced": paced,
            "hard_limits_s": list(maxima),
            "sample_rate": 16000,
            "vad_window_frames": 512,
            "num_threads": 1,
            "threshold": 0.5,
            "min_speech_s": 0.1,
            "min_silence_s": 0.3,
            "native_soft_limit_s": 5,
            "prefix_frames": 8000,
            "suffix_frames": 16000,
            "context_frames": 3200,
            "native_buffer_s": 31,
            "native_active_budget_s": 30,
            "producer": SETTINGS if paced else None,
        },
        "loading_s": {},
        "scope": "실험용 WAV 입력. 동일 PCM의 구간 payload 검증. ASR/마이크/단어 보존 검증 아님.",
    }
    save(output / "worker.json", info)
    try:
        import numpy as np

        info["runtime"] = runtime_info(probe=True)
        with (
            (output / "samples.jsonl").open("x") as log,
            (output / "segment-events.jsonl").open("x") as events,
        ):
            for maximum in maxima:
                began = time.perf_counter()
                vad = create_vad(model, soft_limit_s=5, buffer_s=31)
                info["loading_s"][str(maximum)] = time.perf_counter() - began
                save(output / "worker.json", info)
                for sample in samples:
                    original = load_audio(sample.audio_path, 16000)
                    if sample.audio_sha256 != original.sha256 or original.duration_s > 180:
                        raise ValueError("검증한 180초 이하 원본이 필요합니다.")
                    audio = Audio(
                        array("f", [0] * 8000) + original.samples + array("f", [0] * 16000),
                        16000,
                        original.sha256,
                    )
                    segments, replay = [], {}

                    def emit(row, pcm):
                        segments.append(row)
                        events.write(
                            json.dumps({"id": sample.id, "hard_limit_s": maximum, **row}) + "\n"
                        )
                        events.flush()

                    def factory(vad=vad):
                        return SegmentPipeline(
                            vad,
                            policy,
                            maximum,
                            emit,
                            convert=lambda b: np.asarray(b, dtype=np.float32),
                        )

                    adapter = SegmentReplayAdapter(factory)
                    started, cpu = time.perf_counter(), time.process_time()
                    with RssSampler() as rss:
                        if paced:
                            run_paced(
                                adapter,
                                audio,
                                SETTINGS,
                                output / "events" / f"{sample.id}-{maximum}",
                                replay,
                            )
                        else:
                            stream = adapter.start_stream()
                            for offset in range(0, len(audio.samples), 1600):
                                adapter.accept_chunk(stream, audio.samples[offset : offset + 1600])
                            adapter.finish_stream(stream)
                    wall, used_cpu = time.perf_counter() - started, time.process_time() - cpu
                    meta = adapter.pipeline.metadata()
                    validate_capture(meta, replay if paced else None, expected[sample.id], policy)
                    for row in segments:
                        row["emitted_at_wall_s"] = (
                            row["emitted_monotonic_s"] - replay["t0_monotonic_ns"] / 1e9
                            if paced
                            else None
                        )
                    result = {
                        "id": sample.id,
                        "audio_sha256": original.sha256,
                        "condition": sample.condition,
                        "hard_limit_s": maximum,
                        "source_audio_s": original.duration_s,
                        "pipeline": meta,
                        "segments": segments,
                        "replay": replay if paced else None,
                        "call_wall_s": wall,
                        "process_cpu_s": used_cpu,
                        "resources": rss.metadata(),
                        **source_metrics(
                            segments,
                            8000,
                            8000 + len(original.samples),
                            expected_speech=bool(sample.reference.strip()),
                        ),
                    }
                    log.write(json.dumps(result, ensure_ascii=False) + "\n")
                    log.flush()
                del vad
        info["status"] = "completed"
    except BaseException as error:
        info.update(status="failed", error=repr(error))
        if "replay" in locals():
            info["failed_sample"] = {
                "id": sample.id,
                "hard_limit_s": maximum,
                "replay": replay,
                "pipeline": adapter.pipeline.metadata() if adapter.pipeline else None,
            }
        raise
    finally:
        save(output / "worker.json", info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", type=Path, default=Path("models/silero-vad-k2-20261029/silero_vad.onnx")
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("identity", "rms26"), required=True)
    parser.add_argument("--paced", action="store_true")
    parser.add_argument("--max-segment", type=int, choices=(3, 5, 8))
    args = parser.parse_args()
    probe(
        Path.cwd(),
        args.model,
        args.manifest,
        args.output,
        args.policy,
        paced=args.paced,
        maxima=(args.max_segment,) if args.max_segment else (3, 5, 8),
    )


if __name__ == "__main__":
    raise SystemExit(main())
