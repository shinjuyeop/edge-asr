"""별도 허용된 Silero 파일로 3/5/8초 후보 진단. 파일 다운로드나 ASR 실행은 하지 않는다."""

import argparse
import json
import os
import time
from array import array
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.artifacts import write_json as save
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.resources import RssSampler
from edge_asr.runtime import runtime_info
from edge_asr.vad import create_vad
from edge_asr.vad_probe import collect_segments, source_metrics


def probe(root, model, manifest, output, *, paced=False, maxima=(3, 5, 8)):
    if not model.is_file():
        raise ValueError(
            "허용 후 별도 준비한 Silero ONNX 파일이 필요합니다. 자동 다운로드하지 않습니다."
        )
    if (output / "worker.json").exists():
        raise ValueError("기존 결과를 덮어쓰지 않습니다.")
    samples, manifest_hash = load_manifest(manifest, root)
    if not maxima or any(value not in (3, 5, 8) for value in maxima):
        raise ValueError("3/5/8초 후보가 필요합니다.")
    # native max_speech_duration은 soft limit. 긴 입력도 내부 버퍼를 넘지 않게 파일 길이로 확보.
    import wave

    lengths = []
    for sample in samples:
        with wave.open(str(sample.audio_path), "rb") as handle:
            lengths.append(handle.getnframes() / handle.getframerate())
    if max(lengths) > 180:
        raise ValueError("이 파일 진단의 최대 입력은 180초입니다.")
    buffer_s = max(lengths) + 3
    info = {
        "process_id": os.getpid(),
        "status": "running",
        "model_sha256": digest(model),
        "input_manifest_sha256": manifest_hash,
        "kind": "paced_vad_wav_end_proxy" if paced else "unpaced_vad_audio_timeline",
        "settings": {
            "sample_rate": 16000,
            "num_threads": 1,
            "window_size": 512,
            "threshold": 0.5,
            "min_speech_duration": 0.1,
            "min_silence_duration": 0.3,
            "max_speech_duration_candidates": list(maxima),
            "buffer_size_in_seconds": buffer_s,
            "maximum_is_hard_limit": False,
        },
        "prefix_s": 0.5,
        "suffix_s": 1.0,
        "loading_s": {},
        "scope": "원본 WAV 앞/뒤 합성 무음. 음소 정렬/단어 잘림 검증 아님. paced도 WAV 끝 대리 지연이며 동일 스레드 입력기 사용.",
    }
    save(output / "worker.json", info)
    try:
        import numpy as np

        info["runtime"] = runtime_info(probe=True)
        with (output / "segments.jsonl").open("x") as log:
            for maximum in maxima:
                started = time.perf_counter()
                vad = create_vad(model, soft_limit_s=maximum, buffer_s=buffer_s)
                info["loading_s"][str(maximum)] = time.perf_counter() - started
                save(output / "worker.json", info)
                for sample in samples:
                    audio = load_audio(sample.audio_path, 16000)
                    if sample.audio_sha256 != audio.sha256:
                        raise ValueError("입력 해시 불일치")
                    padded = array("f", [0.0] * 8000) + audio.samples + array("f", [0.0] * 16000)
                    started, cpu = time.perf_counter(), time.process_time()
                    timings = {}
                    with RssSampler() as resources:
                        segments = collect_segments(
                            vad,
                            padded,
                            lambda block: np.asarray(block, dtype=np.float32),
                            paced=paced,
                            telemetry=timings,
                        )
                    wall, cpu = time.perf_counter() - started, time.process_time() - cpu
                    row = {
                        "id": sample.id,
                        "audio_sha256": audio.sha256,
                        "condition": sample.condition,
                        "max_segment_s": maximum,
                        "source_audio_s": audio.duration_s,
                        "input_audio_with_silence_s": len(padded) / 16000,
                        "vad_call_wall_s": wall,
                        "vad_call_cpu_s": cpu,
                        "segments": segments,
                        "timing": timings,
                        "resources": resources.metadata(),
                        "observed_max_segment_s": max(
                            ((s["raw_end_frame"] - s["raw_start_frame"]) / 16000 for s in segments),
                            default=None,
                        ),
                        "segments_exceeding_soft_max": sum(
                            (s["raw_end_frame"] - s["raw_start_frame"]) / 16000 > maximum
                            for s in segments
                        ),
                        **source_metrics(
                            segments,
                            8000,
                            8000 + len(audio.samples),
                            expected_speech=bool(sample.reference.strip()),
                        ),
                    }
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                del vad
        info["status"] = "completed"
    except BaseException as error:
        info.update(status="failed", error=repr(error))
        raise
    finally:
        save(output / "worker.json", info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paced", action="store_true")
    parser.add_argument("--max-segment", type=int, choices=(3, 5, 8))
    args = parser.parse_args()
    probe(
        Path.cwd(),
        args.model,
        args.manifest,
        args.output,
        paced=args.paced,
        maxima=(args.max_segment,) if args.max_segment else (3, 5, 8),
    )


if __name__ == "__main__":
    raise SystemExit(main())
