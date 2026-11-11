"""고정 llama.cpp CPU 빌드에 작은 Qwen C ABI를 연결한다. 기존 패키지는 변경하지 않는다."""

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path

COMMIT = "7d7f0287530bc8981690f361d8c1627dbae1c99a"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source, engine_build, output):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if (
        head != COMMIT
        or subprocess.check_output(["git", "status", "--porcelain"], cwd=source).strip()
    ):
        raise ValueError("고정된 깨끗한 llama.cpp 체크아웃이 필요합니다.")
    cache = (engine_build / "CMakeCache.txt").read_text()
    for value in (
        "GGML_NATIVE:BOOL=ON",
        "GGML_CUDA:BOOL=OFF",
        "GGML_VULKAN:BOOL=OFF",
        "GGML_CPU_KLEIDIAI:BOOL=OFF",
    ):
        if value not in cache:
            raise ValueError(f"초기 CPU 비교의 빌드 옵션이 다릅니다: {value}")
    output.mkdir(parents=True, exist_ok=False)
    cpp = Path(__file__).with_name("bridge.cpp").resolve()
    library = output / "libedge_asr_gguf.so"
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
        "-I",
        str(source / "tools/mtmd"),
        "-I",
        str(source / "vendor/nlohmann"),
        str(cpp),
        "-L",
        str(engine_build / "bin"),
        "-Wl,-rpath," + str(engine_build / "bin"),
        "-lllama",
        "-lmtmd",
        "-lggml",
        "-lggml-base",
        "-pthread",
        "-o",
        str(library),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    receipt = {
        "llama_commit": head,
        "machine": platform.machine(),
        "command": command,
        "compiler": subprocess.check_output(["g++", "--version"], text=True),
        "bridge_source_sha256": sha(cpp),
        "features": ["token_callback_v1", "split_threads_v1"],
        "cmake_cache_sha256": sha(engine_build / "CMakeCache.txt"),
        "engine_libraries": {p.name: sha(p) for p in (engine_build / "bin").glob("*.so")},
        "library_sha256": sha(library) if result.returncode == 0 else None,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "cpu_configuration": "native CPU, GPU OFF, KleidiAI OFF, thread count supplied per model",
    }
    (output / "build.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError(result.stderr)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--engine-build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source.resolve(), args.engine_build.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
