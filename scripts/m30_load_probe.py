"""기존 ASR의 180초 이상 연속 부하 검사. 반드시 m30_thermal_run.py 안에서 실행한다."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edge_asr.adapters import create_adapter
from edge_asr.benchmark import digest
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.evaluation import RssSampler, environment
from m30_thermal_run import save


def probe(root, config_path, manifest_path, output, duration_s=180):
    if not 180 <= duration_s <= 480:
        raise ValueError("부하 시간은 180~480초여야 합니다.")
    if (output / "worker.json").exists():
        raise ValueError("기존 worker 결과를 덮어쓰지 않습니다.")
    config = load_evaluation_config(config_path)
    if config["evaluation"]["mode"] != "batch": raise ValueError("batch 부하 검사가 필요합니다.")
    samples, manifest_hash = load_manifest(manifest_path, root)
    info = {"status": "preparing", "process_id": os.getpid(), "kind": "sustained_load_probe",
            "started_at_utc": datetime.now(timezone.utc).isoformat(), "config": config,
            "config_sha256": digest(config_path), "manifest_sha256": manifest_hash,
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "requested_duration_s": duration_s, "warmup_runs": 3,
            "scope": "모델 1회 로드, 같은 KSS 목록 반복. 처리 시간은 추론 호출만. 연속 부하 시간에는 행 기록과 Python 작업 포함."}
    save(output / "worker.json", info)
    adapter = None
    try:
        audios = [load_audio(s.audio_path, config["audio"]["sample_rate_hz"]) for s in samples]
        if any(s.audio_sha256 != a.sha256 for s, a in zip(samples, audios)):
            raise ValueError("원본 WAV 해시 불일치")
        adapter = create_adapter(config["model"], config["audio"]["sample_rate_hz"], root)
        info["model_files"] = {k: {"path": str(p), "sha256": digest(p)} for k, p in adapter.model_files.items()}
        with RssSampler() as loading:
            start, cpu = time.perf_counter(), time.process_time()
            adapter.load()
            info.update(model_load_s=time.perf_counter()-start, model_load_cpu_s=time.process_time()-cpu)
        info["model_load_resources"] = loading.metadata()
        info["environment"] = environment()
        for _ in range(3): adapter.transcribe(audios[0])
        info["status"] = "running"; save(output / "worker.json", info)
        start = time.perf_counter(); count = 0
        with RssSampler() as usage, (output / "calls.jsonl").open("x") as log:
            while time.perf_counter() - start < duration_s:
                index = count % len(samples)
                began, cpu = time.perf_counter(), time.process_time()
                hypothesis = adapter.transcribe(audios[index])
                wall, cpu = time.perf_counter() - began, time.process_time() - cpu
                row = {"id": samples[index].id, "cycle": count // len(samples),
                       "offset_s": began - start, "inference_s": wall, "cpu_s": cpu,
                       "audio_s": audios[index].duration_s,
                       "hypothesis_sha256": hashlib.sha256(hypothesis.encode()).hexdigest()}
                log.write(json.dumps(row) + "\n"); log.flush(); count += 1
        info.update(status="completed", observed_duration_s=time.perf_counter()-start,
                    completed_calls=count, inference_resources=usage.metadata())
    except BaseException as error:
        info.update(status="failed", error=repr(error)); raise
    finally:
        if adapter: adapter.close()
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save(output / "worker.json", info)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("data/evaluation/kss-dev50/manifest.jsonl"))
    parser.add_argument("--duration", type=float, default=180)
    args = parser.parse_args()
    probe(Path.cwd(), args.config, args.manifest, args.output, args.duration)
