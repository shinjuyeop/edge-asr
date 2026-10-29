"""기존 냉각 기록 재분석 및 선택적 현재 기기 센서 조회. 부하 실행/설정 변경 없음."""

import argparse
import json
from pathlib import Path

from edge_asr.cooling import live_snapshot, summarize_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", type=Path, action="append", default=[], help="기존 실행 폴더; 반복 가능"
    )
    parser.add_argument("--live", action="store_true", help="명령을 실행한 기기의 현재 센서도 읽기")
    parser.add_argument("--output", type=Path, required=True, help="기존 파일 덮어쓰기 거부")
    args = parser.parse_args()
    if not args.run and not args.live:
        parser.error("--run 또는 --live가 필요합니다.")
    if args.output.exists():
        raise FileExistsError(args.output)
    result = {
        "schema": "cooling-audit-v1",
        "historical_runs": [summarize_history(folder) for folder in args.run],
        "live": live_snapshot() if args.live else None,
        "scope": "과거 기록을 수정하지 않음. 순간 센서 조회는 고부하 냉각 합격 판정이 아님.",
    }
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
    print(json.dumps({"output": str(args.output), "live_measured": args.live}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
