"""별도 환경의 Qwen3-ASR 짧은 WAV 로딩·전사 검사. thermal-run 안에서 실행한다."""

import argparse
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.artifacts import digest, write_json
from edge_asr.dataset import load_audio
from edge_asr.resources import RssSampler
from edge_asr.runtime import environment, limits_verified


def probe(root, model_dir, wav, output, *, num_threads=1):
    if type(num_threads) is not int or num_threads not in (1, 2, 4):
        raise ValueError("스레드는 1/2/4 중 하나여야 합니다.")
    worker = output / "worker.json"
    if worker.exists():
        raise FileExistsError("기존 Qwen 검사 결과를 덮어쓰지 않습니다.")
    limits = Path("/proc/self/limits").read_text()
    if not limits_verified(limits):
        raise RuntimeError("thermal-run의 4GiB 주소 공간 제한이 필요합니다.")
    info = {
        "schema_version": 1,
        "kind": "qwen3_isolated_smoke",
        "status": "preparing",
        "process_id": os.getpid(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "limits": limits,
        "environment": environment(),
        "model_load": None,
        "first_inference": None,
        "hypothesis": None,
        "tokens": None,
        "warmup_runs": 0,
        "cer": None,
        "cer_reason": "로딩·첫 전사 검사이며 정답 채점은 후속 공통 평가에서 수행",
        "scope": "로딩과 첫 전사 분리. 자산 해시·WAV 읽기는 로딩/추론 시간에서 제외. steady-state·실시간 평가 아님.",
    }
    write_json(worker, info)

    def measure(key, function):
        info["status"] = key
        write_json(worker, info)
        sampler = RssSampler()
        start, cpu = time.perf_counter(), time.process_time()
        try:
            with sampler:
                return function()
        finally:
            info[key] = {
                "elapsed_s": time.perf_counter() - start,
                "cpu_s": time.process_time() - cpu,
                "resources": sampler.metadata(),
            }
            info["environment"] = environment()
            write_json(worker, info)

    try:
        audio = load_audio(wav, 16000)
        if audio.duration_s > 10:
            raise ValueError("초기 smoke에는 10초 이하 WAV를 사용하세요.")
        info["audio"] = {"path": str(wav), **audio.metadata()}
        names = (
            "conv_frontend.onnx",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "tokenizer/vocab.json",
            "tokenizer/merges.txt",
            "tokenizer/tokenizer_config.json",
        )
        info["model_files"] = {
            name: {"path": str(model_dir / name), "sha256": digest(model_dir / name)}
            for name in names
        }
        options = {
            "conv_frontend": str(model_dir / names[0]),
            "encoder": str(model_dir / names[1]),
            "decoder": str(model_dir / names[2]),
            "tokenizer": str(model_dir / "tokenizer"),
            "num_threads": num_threads,
            "provider": "cpu",
            "sample_rate": 16000,
            "feature_dim": 128,
            "decoding_method": "greedy_search",
            "max_total_len": 512,
            "max_new_tokens": 128,
            "temperature": 1e-6,
            "top_p": 0.8,
            "seed": 42,
            "hotwords": "",
            "debug": True,
        }
        info.update(options=options, stream_language="Korean", use_itn=None)

        def load():
            import sherpa_onnx

            return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(**options)

        recognizer = measure("model_load", load)

        def transcribe():
            import numpy as np

            stream = recognizer.create_stream()
            stream.set_option("language", "Korean")
            stream.accept_waveform(16000, np.frombuffer(audio.samples, dtype=np.float32))
            recognizer.decode_stream(stream)
            return stream.result.text, list(stream.result.tokens)

        text, tokens = measure("first_inference", transcribe)
        if not isinstance(text, str):
            raise TypeError("Qwen이 문자열 전사를 반환하지 않았습니다.")
        info.update(status="completed", hypothesis=text, tokens=tokens)
        return 0
    except Exception as error:
        info.update(status="failed", error=f"{type(error).__name__}: {error}")
        return 1
    finally:
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(worker, info)
        print(f"Qwen smoke: {info['status']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--num-threads", type=int, choices=(1, 2, 4), default=1)
    args = parser.parse_args()
    return probe(
        Path.cwd(),
        args.model_dir.resolve(),
        args.wav.resolve(),
        args.output.resolve(),
        num_threads=args.num_threads,
    )


if __name__ == "__main__":
    raise SystemExit(main())
