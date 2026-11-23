// 고정 Qwen 오디오 그래프의 선택적 Hailo 경계. 기본 CPU 실행은 그대로 둔다.
#include "ggml.h"
#include <hailo/hailort.hpp>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <vector>

namespace {
using namespace hailort;
using Clock = std::chrono::steady_clock;

// [C,H,W]와 [H,W,C] 사이의 작은 타일 전치. 바깥 축은 100-frame 청크다.
void transpose(const float* src, float* dst, size_t batches, size_t rows, size_t cols) {
    constexpr size_t tile = 32;
    for (size_t n = 0; n < batches; ++n) {
        for (size_t i = 0; i < rows; i += tile)
            for (size_t j = 0; j < cols; j += tile)
                for (size_t y = i; y < std::min(i + tile, rows); ++y)
                    for (size_t x = j; x < std::min(j + tile, cols); ++x)
                        dst[x * rows + y] = src[y * cols + x];
        src += rows * cols;
        dst += rows * cols;
    }
}

bool scheduled() {
    int networks = 0;
    for (const char* var : {"EDGE_ASR_HAILO_LAYER", "EDGE_ASR_HAILO_CNN_STACK_HEF",
                            "EDGE_ASR_HAILO_PROJECTION_HEF", "EDGE_ASR_HAILO_FUSED_HEF",
                            "EDGE_ASR_HAILO_EMBED_HEF", "EDGE_ASR_HAILO_CONV23_HEF"}) {
        networks += std::getenv(var) != nullptr;
    }
    // 각 길이의 Transformer 블록은 별도 정적 HEF다.
    return networks > 1 || std::getenv("EDGE_ASR_HAILO_BLOCK_DIR") ||
           std::getenv("EDGE_ASR_HAILO_BLOCK_PAIR_DIR");
}

enum class Boundary { conv, projection, cnn_stack, fused, embed, block, block_pair, conv23 };

const char* boundary_name(Boundary boundary) {
    switch (boundary) {
        case Boundary::fused: return "fused";
        case Boundary::embed: return "embed";
        case Boundary::block: return "block";
        case Boundary::block_pair: return "block_pair";
        case Boundary::conv23: return "conv23";
        case Boundary::conv: return "conv";
        case Boundary::projection: return "projection";
        case Boundary::cnn_stack: return "cnn_stack";
    }
    std::abort();
}

std::shared_ptr<VDevice> qwen_device() {
    static std::mutex lock;
    static std::weak_ptr<VDevice> shared;
    std::lock_guard<std::mutex> guard(lock);
    if (auto existing = shared.lock()) return existing;
    hailo_vdevice_params_t params{};
    if (hailo_init_vdevice_params(&params) != HAILO_SUCCESS)
        throw std::runtime_error("Hailo device parameters missing");
    params.scheduling_algorithm = scheduled()
        ? HAILO_SCHEDULING_ALGORITHM_ROUND_ROBIN : HAILO_SCHEDULING_ALGORITHM_NONE;
    std::shared_ptr<VDevice> device = VDevice::create(params).expect("Hailo device");
    shared = device;
    return device;
}

class QwenNetwork {
    // 두 네트워크 중 마지막 네트워크가 스트림과 그룹을 해제한 뒤 장치를 닫는다.
    std::shared_ptr<VDevice> device;
    std::shared_ptr<ConfiguredNetworkGroup> group;
    std::unique_ptr<ActivatedNetworkGroup> active;
    std::unique_ptr<InferVStreams> pipeline;
    std::string input_name, output_name;
    size_t input_bytes, output_bytes;
    Clock::time_point last_temperature{};
    std::mutex lock;
    std::vector<float> packed_input, packed_output;
public:
    explicit QwenNetwork(const char* file) : device(qwen_device()) {
        if (!file) throw std::runtime_error("missing Qwen HEF");
        auto hef = Hef::create(file).expect("Hailo HEF");
        auto config = device->create_configure_params(hef).expect("Hailo configuration");
        auto groups = device->configure(hef, config).expect("Hailo configure");
        if (groups.size() != 1) throw std::runtime_error("expected one Qwen network group");
        group = groups.front();
        auto inputs = group->make_input_vstream_params(false, HAILO_FORMAT_TYPE_FLOAT32, 10000, 2).expect("inputs");
        auto outputs = group->make_output_vstream_params(false, HAILO_FORMAT_TYPE_FLOAT32, 10000, 2).expect("outputs");
        if (inputs.size() != 1 || outputs.size() != 1) throw std::runtime_error("expected a single Conv");
        input_name = inputs.begin()->first;
        output_name = outputs.begin()->first;
        inputs.begin()->second.user_buffer_format.order = HAILO_FORMAT_ORDER_NHWC;
        outputs.begin()->second.user_buffer_format.order = HAILO_FORMAT_ORDER_NHWC;
        pipeline = std::make_unique<InferVStreams>(InferVStreams::create(*group, inputs, outputs).expect("Conv pipeline"));
        input_bytes = pipeline->get_input_vstreams().front().get().get_frame_size();
        output_bytes = pipeline->get_output_vstreams().front().get().get_frame_size();
        if (!scheduled()) active = group->activate().expect("activate");
    }

    void infer(const ggml_tensor* src, ggml_tensor* dst, Boundary boundary) {
        std::lock_guard<std::mutex> guard(lock);
        const bool rows = boundary == Boundary::projection || boundary == Boundary::embed;
        const bool image_output = boundary == Boundary::conv || boundary == Boundary::cnn_stack ||
                                  boundary == Boundary::conv23;
        const size_t count = rows ? src->ne[1] : src->ne[3];
        if (ggml_nbytes(src) != count * input_bytes || ggml_nbytes(dst) != count * output_bytes ||
            !ggml_is_contiguous(src) || !ggml_is_contiguous(dst))
            throw std::runtime_error("Qwen and HEF frame layout mismatch");
        const auto began = Clock::now();
        const auto* in = static_cast<const float*>(src->data);
        if (image_output && src->ne[2] != 1) {
            packed_input.resize(ggml_nelements(src));
            transpose(in, packed_input.data(), count, src->ne[2], src->ne[0] * src->ne[1]);
            in = packed_input.data();
        }
        auto* out = static_cast<float*>(dst->data);
        if (image_output) {
            packed_output.resize(ggml_nelements(dst));
            out = packed_output.data();
        }
        std::map<std::string, MemoryView> input{{input_name, MemoryView(const_cast<float*>(in), ggml_nbytes(src))}};
        std::map<std::string, MemoryView> output{{output_name, MemoryView(out, ggml_nbytes(dst))}};
        const auto submitted = Clock::now();
        const auto status = pipeline->infer(input, output, count);
        const auto returned = Clock::now();
        if (status != HAILO_SUCCESS) throw std::runtime_error("Hailo infer status " + std::to_string(status));
        if (image_output) transpose(packed_output.data(), static_cast<float*>(dst->data), count, dst->ne[0] * dst->ne[1], dst->ne[2]);
        const double total = std::chrono::duration<double, std::milli>(Clock::now() - began).count();
        const double sync = std::chrono::duration<double, std::milli>(returned - submitted).count();
        std::fprintf(stderr, "edge_hailo_%s frames=%zu sync_ms=%.4f layout_ms=%.4f\n",
                     boundary_name(boundary), count, sync, total - sync);
        if (std::getenv("EDGE_ASR_HAILO_TEMPERATURE") &&
            returned - last_temperature >= std::chrono::seconds(10)) {
            last_temperature = returned;
            auto devices = device->get_physical_devices();
            if (devices && devices->size() == 1) {
                auto value = devices->front().get().get_chip_temperature();
                if (value) std::fprintf(stderr, "edge_hailo_temp_C=%.2f,%.2f\n",
                                        value->ts0_temperature, value->ts1_temperature);
            }
        }
    }
};

template<Boundary boundary>
void compute(ggml_tensor* dst, int thread, int, void* file) {
    if (thread != 0) return;
    try {
        static QwenNetwork engine(static_cast<const char*>(file));
        engine.infer(dst->src[0], dst, boundary);
    } catch (const std::exception& error) {
        // GGML custom op에는 실패 반환값이 없다. 격리한 평가 자식이 잘못된 전사를 반환하지 않게 한다.
        std::fprintf(stderr, "Qwen Hailo inference failed: %s\n", error.what());
        std::abort();
    }
}

template<Boundary boundary>
void compute_block(ggml_tensor* dst, int thread, int, void* directory) {
    if (thread != 0) return;
    try {
        static std::mutex lock;
        static std::map<int64_t, std::unique_ptr<QwenNetwork>> networks;
        std::lock_guard<std::mutex> guard(lock);
        const auto rows = dst->ne[1];
        auto& engine = networks[rows];
        if (!engine) {
            const auto path = std::string(static_cast<const char*>(directory)) +
                              (boundary == Boundary::block ? "/block0" : "/blocks01_") +
                              std::to_string(rows) + ".hef";
            engine = std::make_unique<QwenNetwork>(path.c_str());
        }
        engine->infer(dst->src[0], dst, boundary);
    } catch (const std::exception& error) {
        std::fprintf(stderr, "Qwen Hailo block failed: %s\n", error.what());
        std::abort();
    }
}

ggml_tensor* chunk_input(ggml_tensor* node) {
    if (std::strcmp(node->name, "inp_chunks") == 0) return node;
    for (auto* source : node->src) {
        if (!source) break;
        if (auto* input = chunk_input(source)) return input;
    }
    return nullptr;
}
}

extern "C" ggml_tensor* ggml_conv_2d(ggml_context* ctx, ggml_tensor* weights, ggml_tensor* input,
                                    int sx, int sy, int px, int py, int dx, int dy) {
    static const char* target = std::getenv("EDGE_ASR_HAILO_LAYER");
    if (target && (std::strcmp(target, "a.conv2d.1.weight") == 0 || std::strcmp(target, "a.conv2d.2.weight") == 0) &&
        std::strcmp(weights->name, target) == 0) {
        const bool first = std::strcmp(target, "a.conv2d.1.weight") == 0;
        if (sx != 2 || sy != 2 || px != 1 || py != 1 || dx != 1 || dy != 1 ||
            weights->type != GGML_TYPE_F16 || weights->ne[0] != 3 || weights->ne[1] != 3 ||
            weights->ne[2] != (first ? 1 : 480) || weights->ne[3] != 480 ||
            input->type != GGML_TYPE_F32 || input->ne[0] != (first ? 100 : 50) ||
            input->ne[1] != (first ? 128 : 64) || input->ne[2] != weights->ne[2])
            throw std::runtime_error("unexpected Qwen Conv signature");
        return ggml_custom_4d(ctx, GGML_TYPE_F32, (input->ne[0]+1)/2, (input->ne[1]+1)/2,
                              480, input->ne[3], &input, 1, compute<Boundary::conv>, 1, std::getenv("EDGE_ASR_HAILO_HEF"));
    }
    static auto original = reinterpret_cast<decltype(&ggml_conv_2d)>(dlsym(RTLD_NEXT, "ggml_conv_2d"));
    if (!original) std::abort();
    return original(ctx, weights, input, sx, sy, px, py, dx, dy);
}

extern "C" ggml_tensor* ggml_mul_mat(ggml_context* ctx, ggml_tensor* weights, ggml_tensor* input) {
    static char* fused = std::getenv("EDGE_ASR_HAILO_FUSED_HEF");
    if (fused && std::strcmp(weights->name, "a.conv_out.weight") == 0) {
        auto* chunks = chunk_input(input);
        if (!chunks || chunks->type != GGML_TYPE_F32 || chunks->ne[0] != 100 ||
            chunks->ne[1] != 128 || chunks->ne[2] != 1 || input->ne[1] != chunks->ne[3]*13 ||
            input->ne[0] != 7680 || weights->ne[0] != 7680 || weights->ne[1] != 896)
            throw std::runtime_error("unexpected Qwen fused CNN signature");
        return ggml_custom_4d(ctx, GGML_TYPE_F32, 896, input->ne[1], 1, 1,
                              &chunks, 1, compute<Boundary::fused>, 1, fused);
    }
    static const char* projection = std::getenv("EDGE_ASR_HAILO_PROJECTION_HEF");
    if (projection && std::strcmp(weights->name, "a.conv_out.weight") == 0) {
        if (weights->type != GGML_TYPE_F16 || weights->ne[0] != 7680 || weights->ne[1] != 896 ||
            !ggml_is_matrix(weights) || input->type != GGML_TYPE_F32 || input->ne[0] != 7680 ||
            !ggml_is_matrix(input) || !ggml_is_contiguous(input))
            throw std::runtime_error("unexpected Qwen audio projection signature");
        // 원래 ggml_cont/reshape가 이미 시간 순서의 7680원소 행을 만든다.
        return ggml_custom_4d(ctx, GGML_TYPE_F32, 896, input->ne[1], 1, 1,
                              &input, 1, compute<Boundary::projection>, 1, std::getenv("EDGE_ASR_HAILO_PROJECTION_HEF"));
    }
    static auto original = reinterpret_cast<decltype(&ggml_mul_mat)>(dlsym(RTLD_NEXT, "ggml_mul_mat"));
    if (!original) std::abort();
    return original(ctx, weights, input);
}

extern "C" ggml_tensor* ggml_permute(ggml_context* ctx, ggml_tensor* tensor,
                                     int axis0, int axis1, int axis2, int axis3) {
    static auto original = reinterpret_cast<decltype(&ggml_permute)>(dlsym(RTLD_NEXT, "ggml_permute"));
    if (!original) std::abort();
    static char* stack = std::getenv("EDGE_ASR_HAILO_CNN_STACK_HEF");
    static char* tail = std::getenv("EDGE_ASR_HAILO_CONV23_HEF");
    if (tail && std::strcmp(tensor->name, "after_conv_blocks") == 0) {
        // ggml_conv_2d의 im2col 두 번째 입력까지 따라가 첫 GELU 출력에서 시작한다.
        auto* input = tensor;
        for (int layer = 3; layer >= 2; --layer) {
            if (input->op != GGML_OP_UNARY || ggml_get_unary_op(input) != GGML_UNARY_OP_GELU_ERF ||
                input->src[0]->op != GGML_OP_ADD) throw std::runtime_error("unexpected CNN GELU");
            auto* node = input->src[0]->src[0];
            while (node && node->op != GGML_OP_IM2COL) {
                if (node->op != GGML_OP_MUL_MAT && node->op != GGML_OP_RESHAPE &&
                    node->op != GGML_OP_CONT && node->op != GGML_OP_PERMUTE) break;
                node = node->src[0];
            }
            if (!node || node->op != GGML_OP_IM2COL) throw std::runtime_error("missing CNN im2col");
            input = node->src[1];
        }
        if (input->ne[0] != 50 || input->ne[1] != 64 || input->ne[2] != 480 ||
            input->ne[3] != tensor->ne[3]) throw std::runtime_error("unexpected Conv2 input");
        tensor = ggml_custom_4d(ctx, GGML_TYPE_F32, 13, 16, 480, input->ne[3],
                                &input, 1, compute<Boundary::conv23>, 1, tail);
        ggml_set_name(tensor, "after_conv_blocks");
    }
    if (stack && std::strcmp(tensor->name, "after_conv_blocks") == 0) {
        if (std::getenv("EDGE_ASR_HAILO_LAYER"))
            throw std::runtime_error("choose either the CNN stack or a single Conv");
        // qwen3a가 세 번째 GELU 출력을 투영용으로 재배열하는 경계다.
        // 새 노드는 최초의 Mel 청크만 참조하므로 원래 CPU 합성곱 노드는 실행 그래프에서 제외된다.
        auto* input = chunk_input(tensor);
        if (!input || input->type != GGML_TYPE_F32 || !ggml_is_contiguous(input) ||
            input->ne[0] != 100 || input->ne[1] != 128 || input->ne[2] != 1 ||
            tensor->type != GGML_TYPE_F32 || tensor->ne[0] != 13 || tensor->ne[1] != 16 ||
            tensor->ne[2] != 480 || tensor->ne[3] != input->ne[3] ||
            tensor->op != GGML_OP_UNARY || ggml_get_unary_op(tensor) != GGML_UNARY_OP_GELU_ERF ||
            axis0 != 2 || axis1 != 0 || axis2 != 1 || axis3 != 3)
            throw std::runtime_error("unexpected Qwen CNN stack signature");
        tensor = ggml_custom_4d(ctx, GGML_TYPE_F32, 13, 16, 480, input->ne[3],
                                &input, 1, compute<Boundary::cnn_stack>, 1, stack);
        ggml_set_name(tensor, "after_conv_blocks");
    }
    return original(ctx, tensor, axis0, axis1, axis2, axis3);
}

extern "C" ggml_tensor* ggml_add(ggml_context* ctx, ggml_tensor* a, ggml_tensor* b) {
    static char* block_dir = std::getenv("EDGE_ASR_HAILO_BLOCK_DIR");
    if (block_dir && std::strcmp(b->name, "ffn_out-0") == 0 &&
        std::strcmp(a->name, "ffn_inp-0") == 0) {
        // 첫 residual의 두 번째 피연산자가 위치 임베딩을 더한 원래 블록 입력이다.
        auto* input = a->src[1];
        if (a->op != GGML_OP_ADD || !input || input->ne[0] != 896 ||
            !ggml_is_matrix(input) || input->type != GGML_TYPE_F32)
            throw std::runtime_error("unexpected Qwen first block residual");
        if (input->ne[1] == 26 || input->ne[1] == 52 || input->ne[1] == 78)
            return ggml_custom_4d(ctx, GGML_TYPE_F32, 896, input->ne[1], 1, 1,
                                  &input, 1, compute_block<Boundary::block>, 1, block_dir);
    }
    static char* pair_dir = std::getenv("EDGE_ASR_HAILO_BLOCK_PAIR_DIR");
    if (pair_dir && std::strcmp(b->name, "ffn_out-1") == 0 &&
        std::strcmp(a->name, "ffn_inp-1") == 0 && (a->ne[1] == 13 || a->ne[1] == 26)) {
        if (block_dir) throw std::runtime_error("choose a single first block or a block pair");
        // 1층의 residual 입력에서 0층의 두 residual을 따라 위치 임베딩 직후로 돌아간다.
        auto* first = a->src[1];
        if (a->op != GGML_OP_ADD || !first || first->op != GGML_OP_ADD ||
            std::strcmp(first->name, "layer_out-0") != 0 || !first->src[0] || !first->src[1] ||
            std::strcmp(first->src[0]->name, "ffn_inp-0") != 0 ||
            std::strcmp(first->src[1]->name, "ffn_out-0") != 0)
            throw std::runtime_error("unexpected Qwen first two block residuals");
        auto* input = first->src[0]->src[1];
        if (first->src[0]->op != GGML_OP_ADD || !input || input->type != GGML_TYPE_F32 ||
            !ggml_is_matrix(input) || input->ne[0] != 896 || input->ne[1] != a->ne[1])
            throw std::runtime_error("unexpected Qwen block pair input");
        return ggml_custom_4d(ctx, GGML_TYPE_F32, 896, input->ne[1], 1, 1, &input, 1,
                              compute_block<Boundary::block_pair>, 1, pair_dir);
    }
    static char* embed = std::getenv("EDGE_ASR_HAILO_EMBED_HEF");
    if (embed && std::strcmp(b->name, "mm.a.mlp.2.bias") == 0) {
        // down matmul ← GELU ← bias ← up matmul ← 최종 audio norm 출력
        auto* activation = a->src[1];
        if (a->op != GGML_OP_MUL_MAT || !activation || activation->op != GGML_OP_UNARY ||
            ggml_get_unary_op(activation) != GGML_UNARY_OP_GELU_ERF ||
            activation->src[0]->op != GGML_OP_ADD ||
            activation->src[0]->src[0]->op != GGML_OP_MUL_MAT)
            throw std::runtime_error("unexpected Qwen embedding projector");
        auto* input = activation->src[0]->src[0]->src[1];
        if (!input || std::strcmp(input->name, "after_transformer") != 0 ||
            input->ne[0] != 896 || !ggml_is_matrix(input) || a->ne[0] != 1024)
            throw std::runtime_error("unexpected Qwen embedding dimensions");
        return ggml_custom_4d(ctx, GGML_TYPE_F32, 1024, input->ne[1], 1, 1,
                              &input, 1, compute<Boundary::embed>, 1, embed);
    }
    static auto original = reinterpret_cast<decltype(&ggml_add)>(dlsym(RTLD_NEXT, "ggml_add"));
    if (!original) std::abort();
    return original(ctx, a, b);
}
