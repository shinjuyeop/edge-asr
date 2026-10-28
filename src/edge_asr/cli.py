"""초기 저장소의 설정 확인·환경 정보 CLI. 추론 기능은 아직 제공하지 않는다."""

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
import platform
import sys

from edge_asr import __version__
from edge_asr.config import load_config


def environment_info() -> dict:
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
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="한국어 ASR 연구용 설정·환경 도구")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("env", help="현재 컴퓨터의 환경을 JSON으로 출력")
    check = commands.add_parser("check-config", help="TOML 필수 항목 검사 (추론 실행 아님)")
    check.add_argument("path", help="설정 파일 경로")
    args = parser.parse_args(argv)

    try:
        result = environment_info() if args.command == "env" else load_config(args.path)
    except (OSError, ValueError) as error:
        parser.exit(2, f"오류: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
