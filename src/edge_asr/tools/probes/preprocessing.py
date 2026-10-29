"""RP5 전처리 계산 비용. ASR 호출/입력 수집 지연과 별도로 기록한다."""

import argparse
import hashlib
import json
import os
import sys
import time
import wave
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.artifacts import write_json as save
from edge_asr.audio_policy import POLICIES, preprocess
from edge_asr.dataset import load_audio, load_manifest


def probe(root, data, output):
    if sys.byteorder != "little":
        raise ValueError("이 진단은 little-endian RP5 대상입니다.")
    if (output / "worker.json").exists():
        raise ValueError("기존 결과를 덮어쓰지 않습니다.")
    info = {
        "process_id": os.getpid(),
        "status": "running",
        "kind": "preprocessing_only",
        "source_sha256": digest(root / "src/edge_asr/audio_policy.py"),
        "scope": "이미 읽은 PCM의 preprocess 호출 시간/CPU. 파일 I/O·100ms 입력 수집·ASR은 제외. 매 발화 상태 초기화.",
    }
    save(output / "worker.json", info)
    try:
        samples, _ = load_manifest(data / "dev-identity.jsonl", root)
        with (output / "calls.jsonl").open("x") as log:
            for sample in samples:
                audio = load_audio(sample.audio_path, 16000)
                if sample.audio_sha256 != audio.sha256:
                    raise ValueError("입력 해시 불일치")
                for policy in POLICIES:
                    start, cpu = time.perf_counter(), time.process_time()
                    pcm, meta = preprocess(audio.samples, policy)
                    wall, cpu = time.perf_counter() - start, time.process_time() - cpu
                    with wave.open(str(data / policy / f"{sample.id}.wav"), "rb") as expected:
                        if pcm.tobytes() != expected.readframes(expected.getnframes()):
                            raise ValueError("Host 파생 PCM과 불일치")
                    row = {
                        "id": sample.id,
                        "policy": policy,
                        "audio_s": audio.duration_s,
                        "wall_s": wall,
                        "cpu_s": cpu,
                        "pcm_sha256": hashlib.sha256(pcm.tobytes()).hexdigest(),
                        "newly_clipped_samples": meta["newly_clipped_samples"],
                        "host_pcm_equal": True,
                    }
                    log.write(json.dumps(row) + "\n")
                    log.flush()
        info["status"] = "completed"
    except BaseException as error:
        info.update(status="failed", error=repr(error))
        raise
    finally:
        save(output / "worker.json", info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/evaluation/m30-volume-dev"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    probe(Path.cwd(), args.data, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
