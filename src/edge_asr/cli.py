"""설정·환경 확인, 공통 파일 평가, 저장 가설 재채점 CLI."""

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
import platform
from pathlib import Path
import sys

from edge_asr import __version__
from edge_asr.config import load_config, load_evaluation_config
from edge_asr.runtime import runtime_info


def environment_info(*, probe_runtime=False) -> dict:
    """호출한 컴퓨터의 환경만 기록하며 RP5 여부나 추론 가능성을 보증하지 않는다."""
    packages = {}
    for name in ("numpy", "sherpa-onnx", "sherpa-onnx-core"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "edge_asr": __version__,
        "python": platform.python_version(),
        "executable": sys.executable,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "packages": packages,
        "runtime": runtime_info(probe=probe_runtime),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="한국어 ASR 연구용 설정·환경 도구")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    env = commands.add_parser("env", help="현재 컴퓨터의 환경을 JSON으로 출력")
    env.add_argument("--probe-runtime", action="store_true", help="sherpa를 import하여 네이티브 ORT 버전 확인; 모델 로딩 없음")
    threads = commands.add_parser("threads", help="이미 실행 중인 Linux PID의 TID별 CPU 관찰; 설정 변경 없음")
    threads.add_argument("--pid", type=int, required=True)
    threads.add_argument("--duration-s", type=float, default=3.0)
    threads.add_argument("--interval-s", type=float, default=0.1)
    threads.add_argument("--output", type=Path, help="선택: 새 JSON 파일. 기존 파일은 덮어쓰지 않음")
    check = commands.add_parser("check-config", help="TOML 필수 항목 검사 (추론 실행 아님)")
    check.add_argument("path", help="설정 파일 경로")
    check.add_argument("--evaluation", action="store_true", help="공통 파일 평가용 설정 검사")
    evaluate = commands.add_parser("evaluate", help="여러 WAV의 batch 또는 1배속 paced_stream 평가")
    evaluate.add_argument("--config", type=Path, default=Path("configs/zipformer_eval.toml"))
    evaluate.add_argument("--manifest", type=Path, required=True, help="오디오·정답 JSONL 목록")
    evaluate.add_argument("--root", type=Path, default=Path.cwd(), help="상대 경로 기준, 기본 작업 폴더")
    evaluate.add_argument("--output-dir", type=Path, help="새 고유 실행 폴더를 만들 상위 경로")
    score = commands.add_parser("score", help="모델·오디오 없이 predictions.jsonl 재채점")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--normalization", choices=("ko_cer_v1", "nfc_v1"), default="ko_cer_v1")
    score.add_argument("--references", type=Path, help="선택: 같은 id 집합의 교체 정답 manifest")
    score.add_argument("--root", type=Path, default=Path.cwd())
    score.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "threads":
            from edge_asr.diagnostics import observe_threads
            if args.output is not None and args.output.exists():
                raise ValueError(f"기존 진단 파일을 덮어쓰지 않습니다: {args.output}")
            result = observe_threads(args.pid, args.duration_s, args.interval_s)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x", encoding="utf-8") as handle:
                    json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
                    handle.write("\n")
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0 if result["status"] == "completed" else 1
        if args.command in {"evaluate", "score"}:
            from edge_asr.dataset import resolve_path
            from edge_asr.evaluation import evaluate as run_evaluation, rescore
            root = args.root.expanduser().resolve()
            output = resolve_path(args.output_dir, root) if args.output_dir is not None else None
            if args.command == "evaluate":
                _, code = run_evaluation(resolve_path(args.config, root), resolve_path(args.manifest, root), root, output_base=output)
            else:
                _, code = rescore(resolve_path(args.predictions, root), root, normalization=args.normalization,
                                  reference_manifest=resolve_path(args.references, root) if args.references is not None else None,
                                  output_base=output)
            return code
        result = environment_info(probe_runtime=args.probe_runtime) if args.command == "env" else (load_evaluation_config(args.path) if args.evaluation else load_config(args.path))
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(2, f"오류: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
