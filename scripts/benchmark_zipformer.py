"""설치 없이 기존 RP5 가상환경에서 실행할 수 있는 진입점."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_asr.benchmark import main

if __name__ == "__main__":
    raise SystemExit(main())
