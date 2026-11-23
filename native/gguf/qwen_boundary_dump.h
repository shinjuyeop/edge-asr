// 고정 qwen3a 그래프의 변환 검산 전용. 한 디렉터리는 한 PCM 호출의 단일 encoder 창에 대응한다.
#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "ggml-backend.h"

inline bool write_qwen_tensor(const char* directory, const std::string& name, const ggml_tensor* t) {
    if (t->type != GGML_TYPE_F32 || !ggml_is_contiguous(t)) return false;
    std::string path = std::string(directory) + "/" + name;
    for (int i = 3; i >= 0; --i) path += "_" + std::to_string(t->ne[i]);
    std::vector<float> data(ggml_nelements(t));
    ggml_backend_tensor_get(t, data.data(), 0, data.size() * sizeof(float));
    FILE* file = std::fopen((path + ".f32").c_str(), "wbx");
    if (!file) return false;
    const bool written = std::fwrite(data.data(), sizeof(float), data.size(), file) == data.size();
    return std::fclose(file) == 0 && written;
}

inline bool dump_qwen_boundary(const ggml_tensor* t, const std::string& phase) {
    const char* directory = std::getenv("EDGE_ASR_QWEN_BOUNDARY_DIR");
    if (!directory || !*directory) return true;
    if (phase != "audio_encoder") {
        if (t->op != GGML_OP_MUL_MAT || !t->src[0] || !t->src[1]) return true;
        const char* weight = t->src[0]->name;
        if (std::strcmp(weight, "blk.0.ffn_up.weight") == 0)
            return write_qwen_tensor(directory, phase + "_up_in", t->src[1]) &&
                   write_qwen_tensor(directory, phase + "_up_out", t);
        if (std::strcmp(weight, "blk.0.attn_q.weight") == 0)
            return write_qwen_tensor(directory, phase + "_qkv_in", t->src[1]) &&
                   write_qwen_tensor(directory, phase + "_q_out", t);
        if (std::strcmp(weight, "blk.0.attn_k.weight") == 0)
            return write_qwen_tensor(directory, phase + "_k_out", t);
        if (std::strcmp(weight, "blk.0.attn_v.weight") == 0)
            return write_qwen_tensor(directory, phase + "_v_out", t);
        if (std::strcmp(weight, "output.weight") == 0 && t->ne[1] > 0)
            return write_qwen_tensor(directory, phase + "_head_in", t->src[1]) &&
                   write_qwen_tensor(directory, phase + "_head_out", t);
        return true;
    }
    if (t->op == GGML_OP_CUSTOM && t->src[0] && t->src[1]) {
        // 고정 mtmd_mul_mat_prec의 두 입력. GGML은 b @ a.T를 계산한다.
        if (t->src[0]->ne[0] == 400 && t->src[0]->ne[1] == 402)
            return write_qwen_tensor(directory, "dft_basis", t->src[0]) &&
                   write_qwen_tensor(directory, "dft_frames", t->src[1]) &&
                   write_qwen_tensor(directory, "dft_result", t);
        if (t->src[0]->ne[0] == 201 && t->src[1]->ne[1] == 128)
            return write_qwen_tensor(directory, "mel_power", t->src[0]) &&
                   write_qwen_tensor(directory, "mel_filters", t->src[1]) &&
                   write_qwen_tensor(directory, "mel_result", t);
    }
    const char* name = nullptr;
    for (auto key : {"inp_chunks", "after_conv_blocks", "after_conv_out", "ffn_inp_normed-0",
                     "ffn_out-0", "layer_out-0", "after_transformer", "projected"}) {
        if (std::strcmp(t->name, key) == 0) name = key;
    }
    if (t->op == GGML_OP_ADD && t->src[0] && std::strcmp(t->src[0]->name, "after_conv_out") == 0)
        name = "block_input";
    if (t->op == GGML_OP_ADD && t->ne[0] == 50 && t->ne[1] == 64 && t->ne[2] == 480)
        name = "conv1_bias";
    if (t->ne[2] == 480) {
        if (t->op == GGML_OP_UNARY && t->ne[0] == 50 && t->ne[1] == 64) name = "conv2_input";
        if (t->op == GGML_OP_ADD && t->ne[0] == 25 && t->ne[1] == 32) name = "conv2_bias";
        if (t->op == GGML_OP_UNARY && t->ne[0] == 25 && t->ne[1] == 32) name = "conv3_input";
        if (t->op == GGML_OP_ADD && t->ne[0] == 13 && t->ne[1] == 16) name = "conv3_bias";
    }
    return !name || write_qwen_tensor(directory, name, t);
}
