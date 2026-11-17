"""고정 mtmd에 선택 log-Mel 캐시 preload만 별도 빌드한다. 원래 엔진은 보존한다."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

COMMIT = "7d7f0287530bc8981690f361d8c1627dbae1c99a"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source, mtmd, output):
    if (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        != COMMIT
        or subprocess.check_output(["git", "status", "--porcelain"], cwd=source).strip()
    ):
        raise ValueError("고정된 깨끗한 엔진 소스가 필요합니다.")
    output.mkdir(parents=True, exist_ok=False)
    cpp = Path(__file__).with_name("mel_prefix_cache.cpp").resolve()
    library = output / "libedge_mel_cache.so"
    command = [
        "g++",
        "-std=c++17",
        "-O2",
        "-fPIC",
        "-shared",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        str(source / "include"),
        "-I",
        str(source / "ggml/include"),
        "-isystem",
        str(source / "tools/mtmd"),
        str(cpp),
        "-ldl",
        "-o",
        str(library),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    receipt = {
        "feature": "raw_log_mel_prefix_v1",
        "upstream_commit": COMMIT,
        "library": str(library),
        "library_sha256": digest(library) if result.returncode == 0 else None,
        "mtmd_library": str(mtmd),
        "mtmd_sha256": digest(mtmd),
        "command": command,
        "source_sha256": digest(cpp),
        "header_sha256": digest(cpp.with_suffix(".h")),
        "compiler": subprocess.check_output(["g++", "--version"], text=True),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "cache_persistent_limit_bytes": 1024**2,
        "temporary_limit_bytes": 1024**2,
        "max_mel_frames": 501,
    }
    (output / "build.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError(result.stderr)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--mtmd", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.source.resolve(), a.mtmd.resolve(), a.output.resolve())
