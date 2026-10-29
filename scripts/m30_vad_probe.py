"""별도 허용된 Silero 파일로 3/5/8초 후보 진단. 파일 다운로드나 ASR 실행은 하지 않는다."""
from array import array
import argparse
import json
from pathlib import Path
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edge_asr.benchmark import digest
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.runtime import runtime_info
from edge_asr.vad_probe import collect_segments, source_metrics
from m30_thermal_run import save


def probe(root, model, manifest, output):
    if not model.is_file(): raise ValueError("허용 후 별도 준비한 Silero ONNX 파일이 필요합니다. 자동 다운로드하지 않습니다.")
    if (output / "worker.json").exists(): raise ValueError("기존 결과를 덮어쓰지 않습니다.")
    samples, manifest_hash = load_manifest(manifest, root)
    info = {"process_id": os.getpid(), "status": "running", "model_sha256": digest(model),
            "input_manifest_sha256": manifest_hash, "kind": "unpaced_vad_audio_timeline",
            "settings": {"sample_rate": 16000, "num_threads": 1, "window_size": 512, "threshold": .5,
                         "min_speech_duration": .1, "min_silence_duration": .3, "max_speech_duration_candidates": [3, 5, 8]},
            "prefix_s": .5, "suffix_s": 1.0, "loading_s": {},
            "scope": "원본 WAV 앞/뒤 합성 무음. 빠른 파일 입력의 알고리즘 시간축. 1배속 실제 endpoint 지연·단어 잘림 검증 아님."}
    save(output / "worker.json", info)
    try:
        import numpy as np
        import sherpa_onnx as sherpa
        info["runtime"] = runtime_info()
        with (output / "segments.jsonl").open("x") as log:
            for maximum in (3, 5, 8):
                config = sherpa.VadModelConfig()
                config.sample_rate = 16000; config.num_threads = 1; config.provider = "cpu"
                for key, value in dict(model=str(model.resolve()), threshold=.5, min_speech_duration=.1,
                                       min_silence_duration=.3, max_speech_duration=maximum, window_size=512).items():
                    setattr(config.silero_vad, key, value)
                started = time.perf_counter()
                vad = sherpa.VoiceActivityDetector(config, buffer_size_in_seconds=maximum + 5)
                info["loading_s"][str(maximum)] = time.perf_counter() - started
                save(output / "worker.json", info)
                for sample in samples:
                    audio = load_audio(sample.audio_path, 16000)
                    if sample.audio_sha256 != audio.sha256: raise ValueError("입력 해시 불일치")
                    padded = array("f", [0.0] * 8000) + audio.samples + array("f", [0.0] * 16000)
                    started, cpu = time.perf_counter(), time.process_time()
                    segments = collect_segments(vad, padded, lambda block: np.asarray(block, dtype=np.float32))
                    wall, cpu = time.perf_counter() - started, time.process_time() - cpu
                    row = {"id": sample.id, "audio_sha256": audio.sha256, "condition": sample.condition,
                           "max_segment_s": maximum, "source_audio_s": audio.duration_s,
                           "vad_call_wall_s": wall, "vad_call_cpu_s": cpu, "segments": segments,
                           **source_metrics(segments, 8000, 8000 + len(audio.samples), expected_speech=bool(sample.reference.strip()))}
                    log.write(json.dumps(row, ensure_ascii=False) + "\n"); log.flush()
                del vad
        info["status"] = "completed"
    except BaseException as error:
        info.update(status="failed", error=repr(error)); raise
    finally:
        save(output / "worker.json", info)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe(Path.cwd(), args.model, args.manifest, args.output)
