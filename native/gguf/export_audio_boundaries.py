"""고정 Qwen GGUF와 실제 특징 계수에서 검산할 정적 ONNX 경계들을 내보낸다. Host 전용 선택 도구."""

import argparse
from pathlib import Path

import gguf
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from edge_asr.artifacts import digest
from edge_asr.qwen.gguf import MODEL_FILES


def export(weights, kind, tokens, path):
    nodes, initializers = [], {}

    def constant(name, value):
        if name not in initializers:
            initializers[name] = numpy_helper.from_array(np.asarray(value), name)
        return name

    def weight(name, *, transpose=False, flatten=False):
        key = name + (".T" if transpose else "")
        if key not in initializers:
            tensor = weights[name]
            array = gguf.dequantize(tensor.data, tensor.tensor_type).astype(np.float32)
            if flatten:
                array = array.reshape(-1)
            constant(key, array.T if transpose else array)
        return key

    def op(operation, *inputs, **attributes):
        output = f"v{len(nodes)}"
        nodes.append(helper.make_node(operation, list(inputs), [output], name=output, **attributes))
        return output

    def linear(x, prefix, bias=True):
        y = op("MatMul", x, weight(prefix + ".weight", transpose=True))
        return op("Add", y, weight(prefix + ".bias")) if bias else y

    def gelu(x):
        half = constant("half", np.float32(0.5))
        root = constant("sqrt_two", np.float32(np.sqrt(2)))
        one = constant("one", np.float32(1))
        # DFC 3.x가 GELU로 인식하는 곱셈 순서. Qwen의 Erf와 계수는 그대로 쓴다.
        cdf = op("Add", one, op("Erf", op("Div", x, root)))
        return op("Mul", op("Mul", x, cdf), half)

    def conv(x, i, bias=True):
        prefix = f"a.conv2d.{i}"
        return op(
            "Conv",
            x,
            weight(prefix + ".weight"),
            *([weight(prefix + ".bias", flatten=True)] if bias else []),
            kernel_shape=[3, 3],
            strides=[2, 2],
            pads=[1, 1, 1, 1],
        )

    def reshape(x, shape):
        return op("Reshape", x, constant(f"shape_{len(nodes)}", np.array(shape, dtype=np.int64)))

    def projection(x):
        x = reshape(op("Transpose", x, perm=[0, 3, 1, 2]), [1, 13, 7680])
        return linear(x, "a.conv_out", bias=False)

    def ffn(x):
        return linear(gelu(linear(x, "a.blk.0.ffn_up")), "a.blk.0.ffn_down")

    def norm(x, number):
        prefix = f"a.blk.0.ln{number}"
        return op(
            "LayerNormalization",
            x,
            weight(prefix + ".weight"),
            weight(prefix + ".bias"),
            axis=-1,
            epsilon=1e-5,
        )

    x = "input"
    if kind in ("conv1", "conv1_linear", "cnn_projection", "cnn_stack", "cnn_projected"):
        input_shape = [1, 1, 128, 100]
        x = conv(x, 1, bias=kind != "conv1_linear")
        output_shape = [1, 480, 64, 50]
        if kind in ("cnn_projection", "cnn_stack", "cnn_projected"):
            x = gelu(conv(gelu(conv(gelu(x), 2)), 3))
            output_shape = [1, 480, 16, 13]
        if kind == "cnn_projection":
            x = projection(x)
            output_shape = [1, 13, 896]
        if kind == "cnn_projected":
            # 각 시간 위치에서 원래 투영의 열은 c*16+f 순서다.
            tensor = weights["a.conv_out.weight"]
            matrix = gguf.dequantize(tensor.data, tensor.tensor_type).astype(np.float32)
            kernel = constant("conv_out_kernel", matrix.reshape(896, 480, 16, 1))
            x = op("Conv", x, kernel, kernel_shape=[16, 1])
            output_shape = [1, 896, 1, 13]
    elif kind == "conv23":
        input_shape, output_shape = [1, 480, 64, 50], [1, 480, 16, 13]
        x = gelu(conv(gelu(conv(x, 2)), 3))
    elif kind in ("conv2", "conv3", "conv2_linear"):
        second = kind.startswith("conv2")
        input_shape = [1, 480, 64, 50] if second else [1, 480, 32, 25]
        output_shape = [1, 480, 32, 25] if second else [1, 480, 16, 13]
        x = conv(x, 2 if second else 3, bias=kind != "conv2_linear")
    elif kind == "conv_out":
        input_shape, output_shape = [1, 480, 16, 13], [1, 13, 896]
        x = projection(x)
    elif kind == "block0":
        input_shape = output_shape = [1, tokens, 896]
        h = norm(x, 1)
        q, k, v = [
            op(
                "Transpose",
                reshape(linear(h, "a.blk.0.attn_" + role), [1, tokens, 14, 64]),
                perm=axes,
            )
            for role, axes in (("q", [0, 2, 1, 3]), ("k", [0, 2, 3, 1]), ("v", [0, 2, 1, 3]))
        ]
        scores = op("Mul", op("MatMul", q, k), constant("scale", np.float32(1 / 8)))
        attention = op("MatMul", op("Softmax", scores, axis=-1), v)
        merged = reshape(op("Transpose", attention, perm=[0, 2, 1, 3]), input_shape)
        residual = op("Add", x, linear(merged, "a.blk.0.attn_out"))
        x = op("Add", residual, ffn(norm(residual, 2)))
    elif kind == "ffn0":
        input_shape = output_shape = [1, tokens, 896]
        x = ffn(x)
    elif kind == "projector":
        input_shape, output_shape = [1, tokens, 896], [1, tokens, 1024]
        x = linear(gelu(linear(x, "mm.a.mlp.1")), "mm.a.mlp.2")
    else:
        raise ValueError(kind)
    graph = helper.make_graph(
        nodes,
        kind,
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, input_shape)],
        [helper.make_tensor_value_info(x, TensorProto.FLOAT, output_shape)],
        list(initializers.values()),
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
    onnx.checker.check_model(model, full_check=True)
    with path.open("xb") as file:
        file.write(model.SerializeToString())


def export_frame_matrices(directory, text_gguf, output, *, qkv=False):
    """DFT·Mel·LLM FFN up은 행별 독립 선형 변환이다. 실제 GGML 방향을 유지한다."""
    if digest(text_gguf) != MODEL_FILES["Qwen3-ASR-0.6B-Q8_0.gguf"]:
        raise ValueError("현재 Q8 text GGUF 해시와 일치해야 합니다.")
    reader = gguf.GGUFReader(text_gguf)
    tensors = {t.name: t for t in reader.tensors}

    def matrix(name):
        tensor = tensors[name]
        return gguf.dequantize(tensor.data, tensor.tensor_type).reshape(-1, 1024)

    matrices = {"llm_up": matrix("blk.0.ffn_up.weight")}
    if qkv:
        matrices["llm_qkv"] = np.concatenate(
            [matrix(f"blk.0.attn_{role}.weight") for role in ("q", "k", "v")]
        )
    for name, rows, columns in (("dft_basis", 402, 400), ("mel_filters", 128, 201)):
        file = directory / f"{name}_1_1_{rows}_{columns}.f32"
        matrices[name] = np.fromfile(file, dtype="<f4").reshape(rows, columns)
    for name, matrix in matrices.items():
        rows, columns = matrix.shape
        graph = helper.make_graph(
            [helper.make_node("MatMul", ["input", "weight"], ["output"], name=name)],
            name,
            [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, columns])],
            [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, rows])],
            [numpy_helper.from_array(matrix.T.copy(), "weight")],
        )
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
        onnx.checker.check_model(model, full_check=True)
        with (output / f"{name}.onnx").open("xb") as file:
            file.write(model.SerializeToString())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audio-gguf", required=True, type=Path)
    p.add_argument("--tokens", required=True, type=int, nargs="+")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--linear-reference", type=Path, help="실제 DFT·Mel 계수를 추출한 호출 디렉터리")
    p.add_argument("--text-gguf", type=Path, help="첫 LLM FFN up 투영의 원래 Q8 가중치")
    p.add_argument("--qkv", action="store_true", help="첫 LLM 블록의 Q/K/V를 행 방향으로 묶음")
    args = p.parse_args()
    if args.qkv and not args.text_gguf:
        p.error("--qkv에는 --text-gguf가 필요합니다.")
    if bool(args.linear_reference) != bool(args.text_gguf):
        p.error("--linear-reference와 --text-gguf를 함께 지정하세요.")
    if digest(args.audio_gguf) != MODEL_FILES["mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"]:
        p.error("현재 Q8 mmproj 해시와 일치해야 합니다.")
    if any(n <= 0 or n % 13 or n > 104 for n in args.tokens):
        p.error("한 encoder 창에서 패딩을 포함한 실제 13~104 토큰 수를 지정하세요.")
    args.output.mkdir(parents=True, exist_ok=False)
    if args.linear_reference:
        export_frame_matrices(args.linear_reference, args.text_gguf, args.output, qkv=args.qkv)
    weights = {w.name: w for w in gguf.GGUFReader(args.audio_gguf).tensors}
    for kind in (
        "conv1",
        "conv2",
        "conv3",
        "conv1_linear",
        "conv2_linear",
        "conv_out",
        "cnn_projection",
        "cnn_stack",
        "cnn_projected",
        "conv23",
    ):
        export(weights, kind, None, args.output / f"{kind}.onnx")
    for kind in ("ffn0", "projector"):
        export(weights, kind, 1, args.output / f"{kind}-row.onnx")
    for tokens in sorted(set(args.tokens)):
        for kind in ("block0", "ffn0", "projector"):
            export(weights, kind, tokens, args.output / f"{kind}-{tokens}.onnx")


if __name__ == "__main__":
    main()
