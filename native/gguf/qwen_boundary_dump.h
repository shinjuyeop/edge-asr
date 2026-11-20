// 고정 qwen3a 그래프의 변환 검산 전용. 한 디렉터리는 한 PCM 호출의 단일 encoder 창에 대응한다.
#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>
#include "ggml-backend.h"

inline bool dump_qwen_boundary(const ggml_tensor* t) {
    const char* directory = std::getenv("EDGE_ASR_QWEN_BOUNDARY_DIR");
    if (!directory || !*directory) return true;
    const char* name = nullptr;
    for (auto key : {"inp_chunks", "after_conv_blocks", "after_conv_out", "ffn_inp_normed-0",
                     "ffn_out-0", "layer_out-0", "after_transformer", "projected"}) {
        if (std::strcmp(t->name, key) == 0) name = key;
    }
    if (t->op == GGML_OP_ADD && t->src[0] && std::strcmp(t->src[0]->name, "after_conv_out") == 0)
        name = "block_input";
    if (t->op == GGML_OP_ADD && t->ne[0] == 50 && t->ne[1] == 64 && t->ne[2] == 480)
        name = "conv1_bias";
    if (!name) return true;
    if (t->type != GGML_TYPE_F32 || !ggml_is_contiguous(t)) return false;
    std::string path = std::string(directory) + "/" + name;
    for (int i = 3; i >= 0; --i) path += "_" + std::to_string(t->ne[i]);
    std::unique_ptr<FILE, decltype(&std::fclose)> file(std::fopen((path + ".f32").c_str(), "wbx"), std::fclose);
    if (!file) return false;
    std::vector<float> data(ggml_nelements(t));
    ggml_backend_tensor_get(t, data.data(), 0, data.size() * sizeof(float));
    return std::fwrite(data.data(), sizeof(float), data.size(), file.get()) == data.size() &&
           std::fclose(file.release()) == 0;
}
