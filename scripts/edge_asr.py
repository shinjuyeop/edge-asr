"""기존 RP5 가상환경에 재설치 없이 공통 CLI를 사용하는 진입점."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_asr.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
