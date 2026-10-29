"""배포처 전사와 고정된 WAV 해시로 스피커 없이 첫 평가 목록을 준비한다."""

import argparse
import hashlib
import json
from pathlib import Path

REVISION = "ba6078bca4daf3f0dd37f79d0ab505af71df14a6"
SOURCE = "https://huggingface.co/k2-fsa/sherpa-onnx-streaming-zipformer-korean-2024-06-16"
WAV = "models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/test_wavs/0.wav"
WAV_SHA256 = "0faf0b037efe428e5e561195f4d2aa148b2a0a2a5fc540b2c184b9d5c241e984"
REFERENCE = "그는 괜찮은 척하려고 애쓰는 것 같았다."


def prepare(root: Path, output: Path) -> Path:
    actual = hashlib.sha256((root / WAV).read_bytes()).hexdigest()
    if actual != WAV_SHA256:
        raise ValueError(
            f"기존 0.wav가 고정한 공식 샘플과 다릅니다. 수정하지 않고 중단합니다. SHA256: {actual}"
        )
    row = {
        "id": "zipformer-official-test-0",
        "audio_path": WAV,
        "audio_sha256": WAV_SHA256,
        "reference": REFERENCE,
        "split": "smoke_test",
        "condition": "official_example",
        "reference_source": f"{SOURCE}/blob/{REVISION}/test_wavs/trans.txt",
        "audio_source": f"{SOURCE}/resolve/{REVISION}/test_wavs/0.wav",
    }
    content = json.dumps(row, ensure_ascii=False) + "\n"
    if output.exists():
        if output.read_text(encoding="utf-8") != content:
            raise ValueError(f"기존 평가 목록을 덮어쓰지 않습니다: {output}")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="공식 한국어 0.wav 전사로 로컬 동작 확인 목록 생성"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/official-test0.jsonl"))
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    output = args.output.expanduser()
    output = (output if output.is_absolute() else root / output).resolve()
    try:
        prepare(root, output)
    except (OSError, ValueError) as error:
        parser.exit(2, f"오류: {error}\n")
    print(f"공식 샘플 해시 일치. 배포처 전사로 평가 목록 준비 완료: {output}")
    print("단일 배포 샘플의 동작 확인용이며 일반 한국어 정확도를 대표하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
