"""고정 BF16에서 양자화와 출력층 후보를 독립 생성한다. 기존 자산은 덮어쓰지 않는다."""

import argparse
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from edge_asr.artifacts import digest, write_json
from edge_asr.qwen.gguf import BF16_SHA256, LLAMA_COMMIT, MODEL_FILES


def prepare(
    source, tool, llama_source, audio_model, output, *, quantizations=None, output_tensor_type=None
):
    quantizations = quantizations or ("q6_k", "q5_k_m", "q4_k_m")
    if any(q not in ("q6_k", "q5_k_m", "q4_k_m", "q4_0") for q in quantizations):
        raise ValueError("지원하지 않는 text 양자화입니다.")
    if output_tensor_type not in (None, "q4_0", "q5_k", "q6_k", "q8_0"):
        raise ValueError("지원하지 않는 출력층 형식입니다.")
    if len(set(quantizations)) != len(quantizations):
        raise ValueError("같은 출력 양자화를 중복 지정할 수 없습니다.")
    if digest(source) != BF16_SHA256:
        raise ValueError("고정된 공식 BF16 원본이 필요합니다. 재양자화 입력은 허용하지 않습니다.")
    if digest(audio_model) != MODEL_FILES["mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"]:
        raise ValueError("기존 Q8 오디오 mmproj가 필요합니다.")
    if (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=llama_source, text=True).strip()
        != LLAMA_COMMIT
    ):
        raise ValueError("고정된 llama.cpp 소스가 필요합니다.")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=llama_source).strip():
        raise ValueError("upstream 소스가 수정되어 있습니다.")
    sys.path.insert(0, str(llama_source / "gguf-py"))
    import gguf

    output.mkdir(parents=True, exist_ok=False)
    for quant in quantizations:
        variant = quant + (f"-head-{output_tensor_type}" if output_tensor_type else "")
        model = output / f"Qwen3-ASR-0.6B-{variant.upper()}.gguf"
        command = [str(tool)]
        if output_tensor_type:
            command.extend(["--output-tensor-type", output_tensor_type])
        command.extend([str(source), str(model), quant.upper(), "2"])
        began = time.perf_counter()
        with (output / f"{variant}.log").open("x") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        reader = gguf.GGUFReader(str(model))
        file_type = reader.fields["general.file_type"].contents()
        if file_type != gguf.LlamaFileType["MOSTLY_" + quant.upper()].value:
            raise ValueError("출력 GGUF file_type이 요청한 양자화와 다릅니다.")
        head = next(t for t in reader.tensors if t.name == "output.weight")
        expected_head = (output_tensor_type or "q6_k").upper()
        if head.tensor_type.name != expected_head or head.shape.tolist() != [1024, 151936]:
            raise ValueError("출력층 tensor의 실제 형식·차원이 요청과 다릅니다.")
        assets = {
            "schema_version": 1,
            "quantization": quant,
            "source_sha256": BF16_SHA256,
            "llama_commit": LLAMA_COMMIT,
            "text": {"path": model.name, "sha256": digest(model), "bytes": model.stat().st_size},
            "audio": {
                "path": os.path.relpath(audio_model, output),
                "sha256": digest(audio_model),
                "quantization": "q8_0",
            },
            "conversion": {
                "command": command,
                "tool_sha256": digest(tool),
                "source": str(source),
                "script_sha256": digest(Path(__file__)),
                "elapsed_s": time.perf_counter() - began,
                "file_type": file_type,
                "output_tensor_type_override": output_tensor_type,
                "output_tensor": {
                    "name": head.name,
                    "type": head.tensor_type.name,
                    "shape": head.shape.tolist(),
                    "bytes": head.n_bytes,
                },
                "tensor_type_counts": dict(Counter(t.tensor_type.name for t in reader.tensors)),
                "tensor_type_bytes": {
                    kind: sum(t.n_bytes for t in reader.tensors if t.tensor_type.name == kind)
                    for kind in {t.tensor_type.name for t in reader.tensors}
                },
            },
        }
        write_json(output / f"{variant}.json", assets)
        print(f"{variant}: {model.stat().st_size} bytes {assets['text']['sha256']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "tool", "llama-source", "audio-model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--quantization", action="append", choices=("q6_k", "q5_k_m", "q4_k_m", "q4_0")
    )
    parser.add_argument("--output-tensor-type", choices=("q4_0", "q5_k", "q6_k", "q8_0"))
    args = parser.parse_args()
    prepare(
        args.source.resolve(),
        args.tool.resolve(),
        args.llama_source.resolve(),
        args.audio_model.resolve(),
        args.output.resolve(),
        quantizations=args.quantization,
        output_tensor_type=args.output_tensor_type,
    )


if __name__ == "__main__":
    main()
