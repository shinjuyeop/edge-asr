"""과거 M1 Zipformer 재현용 진입점. 현재 Qwen은 tools benchmark-qwen3를 사용한다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_asr.legacy.benchmark_zipformer import main

if __name__ == "__main__":
    raise SystemExit(main())
