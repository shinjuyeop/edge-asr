"""WAV 1배속 터미널 자막 MVP 실행 도구. 기존 열 감시/동시 추론을 연결한다."""

import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest, resolve_path
from edge_asr.legacy.realtime.captions import PREFIX, render
from edge_asr.legacy.tools.reports.captions import summarize

REFINE_CONFIGS = {"sensevoice": "sensevoice_eval.toml", "qwen3": "qwen3_eval.toml"}


def start(root, *, wav=None, manifest=None, output=None, refine_model="sensevoice"):
    from edge_asr.tools.probes.thermal import run

    if (wav is None) == (manifest is None):
        raise ValueError("--wav 또는 --manifest 중 하나가 필요합니다.")
    if refine_model not in REFINE_CONFIGS:
        raise ValueError("보정 모델은 sensevoice 또는 qwen3여야 합니다.")
    if output is not None and output.exists():
        raise FileExistsError("기존 출력 폴더를 덮어쓰지 않습니다.")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip():
        raise ValueError("소스 변경을 commit/push/pull한 뒤 실행하세요.")
    if sys.platform != "linux":
        raise ValueError("현재 MVP 실행은 기존 Linux RP5 런타임을 사용합니다.")
    for filename in ("zipformer_paced.toml", REFINE_CONFIGS[refine_model]):
        config = load_evaluation_config(root / "configs/legacy" / filename)
        for path in create_adapter(config["model"], 16000, root).model_files.values():
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(f"모델 파일이 없습니다: {path}")
    from edge_asr.vad import MODEL_SHA

    if digest(root / "models/silero-vad-k2-20261029/silero_vad.onnx") != MODEL_SHA:
        raise ValueError("기존 VAD 모델 해시가 다릅니다.")
    if wav:
        audio = load_audio(wav, 16000)
        duration = audio.duration_s
        rows = [
            {
                "id": wav.stem,
                "reference": "",
                "condition": "untranscribed_demo",
                "audio_path": str(wav),
                "audio_sha256": audio.sha256,
            }
        ]
    else:
        samples, _ = load_manifest(manifest, root)
        duration = 0
        for sample in samples:
            audio = load_audio(sample.audio_path, 16000)
            if sample.audio_sha256 != audio.sha256:
                raise ValueError("manifest 원본 WAV 해시가 필요합니다.")
            duration += audio.duration_s
    if duration > 480:
        raise ValueError("첫 MVP는 외부 600초 제한 안에서 최대 480초 WAV만 지원합니다.")
    del audio
    directory = output or root / "results/live" / (
        datetime.now(timezone.utc).strftime("wav-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    )
    directory.mkdir(parents=True, exist_ok=False)
    if wav:
        manifest = directory / "input.jsonl"
        write_jsonl(manifest, rows)
    print(
        f"결과: {directory}\n보정 모델: {refine_model} (2스레드)\n"
        "모델 준비 후 WAV를 1배속으로 입력합니다. 보정 후보는 별도 표시합니다.",
        flush=True,
    )
    command = [
        sys.executable,
        "-u",
        str(root / "scripts/edge_asr.py"),
        "tools",
        "probe-concurrent",
        "--manifest",
        str(manifest),
        "--output",
        str(directory / "runtime"),
        "--zip-threads",
        "1",
        "--refine-threads",
        "2",
        "--refine-policy",
        "vad0",
        "--caption-display",
    ]
    if refine_model != "sensevoice":
        command += ["--refine-config", str(root / "configs/legacy" / REFINE_CONFIGS[refine_model])]
    with (directory / "terminal-events.jsonl").open("x") as terminal:

        def display(line):
            if line.startswith(PREFIX):
                event = json.loads(line[len(PREFIX) :])
                began = time.perf_counter_ns()
                print(render(event), flush=True)
                terminal.write(
                    json.dumps(
                        {
                            "event": event,
                            "stdout_started_ns": began,
                            "stdout_flushed_ns": time.perf_counter_ns(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                terminal.flush()

        code = run(
            root,
            directory / "runtime",
            command,
            temperature_policy="operational",
            console_callback=display,
        )
    result = {
        "schema": "separate-caption-mvp-v1",
        "status": "failed_or_unverified",
        "automatic_replacements": 0,
    }
    try:
        if code == 0:
            result = summarize(directory, manifest != directory / "input.jsonl")
    except (OSError, ValueError, KeyError) as error:
        result["error"] = repr(error)
        code = 1
    write_json(directory / "summary.json", result)
    print(f"종료: {result['status']} · 결과 {directory}", flush=True)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wav", type=Path, help="16kHz mono PCM16 WAV")
    source.add_argument("--manifest", type=Path, help="기존 해시 포함 평가 manifest")
    parser.add_argument("--output", type=Path, help="새 실행 폴더; 기본 results/live/고유ID")
    parser.add_argument(
        "--refine-model",
        choices=tuple(REFINE_CONFIGS),
        default="sensevoice",
        help="별도 보정 후보 모델, 기본 sensevoice; qwen3는 별도 Qwen+VAD 환경 필요",
    )
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        return start(
            root,
            wav=resolve_path(args.wav, root) if args.wav else None,
            manifest=resolve_path(args.manifest, root) if args.manifest else None,
            output=resolve_path(args.output, root) if args.output else None,
            refine_model=args.refine_model,
        )
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"오류: {error}\n")
