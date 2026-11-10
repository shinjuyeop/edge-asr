"""공식 ORT 1.28.2 헤더를 검사하고 별도 경로에 계측 공유 라이브러리를 빌드한다."""

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(headers, output):
    expected = {
        "onnxruntime_c_api.h": "b69a133143c0da61782b2b02cc8a620d21ac139231fd211719776d10385135c1",
        "onnxruntime_error_code.h": "5ce3b054e798eced8d14f5b86e98692fd33470463f96194ce0700a2d53dd8721",
        "onnxruntime_ep_c_api.h": "db86df0f846b8d3bdbf429a5c76c5805293196575ca3c475878612591cd18e51",
    }
    for name, want in expected.items():
        if not (headers / name).is_file():
            raise FileNotFoundError(headers / name)
        if sha(headers / name) != want:
            raise ValueError(f"확인한 ORT v1.28.2 헤더와 SHA-256이 다릅니다: {name}")
    if "#define ORT_API_VERSION 28" not in (headers / "onnxruntime_c_api.h").read_text():
        raise ValueError("ORT API 28 헤더가 필요합니다.")
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parent
    library = output / "libedge_asr_ort_trace.so"
    command = [
        "g++",
        "-std=c++17",
        "-O2",
        "-fPIC",
        "-shared",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-pthread",
        "-I",
        str(headers),
        str(source / "ort_trace.cc"),
        "-ldl",
        "-Wl,--version-script=" + str(source / "ort_trace.map"),
        "-o",
        str(library),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    info = {
        "machine": platform.machine(),
        "command": command,
        "exit_code": result.returncode,
        "compiler": subprocess.check_output(["g++", "--version"], text=True),
        "headers": {name: sha(headers / name) for name in expected},
        "sources": {name: sha(source / name) for name in ("ort_trace.cc", "ort_trace.map")},
        "library_sha256": sha(library) if result.returncode == 0 else None,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    (output / "build.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError(result.stderr)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.headers.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
