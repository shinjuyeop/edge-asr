// Qwen의 지정한 FP16 합성곱 한 개만 Hailo-8L의 무편향 HEF로 실행하는 실험용 preload.
// 원래 qwen3a 그래프가 bias와 GELU를 이어서 계산한다.
#include "ggml.h"
#include <hailo/hailort.hpp>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
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

class HailoConv {
    std::unique_ptr<VDevice> device;
    std::shared_ptr<ConfiguredNetworkGroup> group;
    std::unique_ptr<ActivatedNetworkGroup> active;
    std::unique_ptr<InferVStreams> pipeline;
    std::string input_name, output_name;
    size_t input_bytes, output_bytes;
    std::mutex lock;
    std::vector<float> packed_input, packed_output;
public:
    explicit HailoConv(const char* file) {
        hailo_vdevice_params_t params{};
        if (!file || hailo_init_vdevice_params(&params) != HAILO_SUCCESS)
            throw std::runtime_error("Hailo HEF or device parameters missing");
        params.scheduling_algorithm = HAILO_SCHEDULING_ALGORITHM_NONE;
        device = VDevice::create(params).expect("Hailo device");
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
        active = group->activate().expect("activate");
    }

    void infer(const ggml_tensor* src, ggml_tensor* dst) {
        std::lock_guard<std::mutex> guard(lock);
        const size_t count = src->ne[3];
        if (ggml_nbytes(src) != count * input_bytes || ggml_nbytes(dst) != count * output_bytes ||
            !ggml_is_contiguous(src) || !ggml_is_contiguous(dst))
            throw std::runtime_error("Qwen and HEF frame layout mismatch");
        const auto began = Clock::now();
        const auto* in = static_cast<const float*>(src->data);
        if (src->ne[2] != 1) {
            packed_input.resize(ggml_nelements(src));
            transpose(in, packed_input.data(), count, src->ne[2], src->ne[0] * src->ne[1]);
            in = packed_input.data();
        }
        packed_output.resize(ggml_nelements(dst));
        std::map<std::string, MemoryView> input{{input_name, MemoryView(const_cast<float*>(in), ggml_nbytes(src))}};
        std::map<std::string, MemoryView> output{{output_name, MemoryView(packed_output.data(), ggml_nbytes(dst))}};
        const auto submitted = Clock::now();
        const auto status = pipeline->infer(input, output, count);
        const auto returned = Clock::now();
        if (status != HAILO_SUCCESS) throw std::runtime_error("Hailo infer status " + std::to_string(status));
        transpose(packed_output.data(), static_cast<float*>(dst->data), count, dst->ne[0] * dst->ne[1], dst->ne[2]);
        const double total = std::chrono::duration<double, std::milli>(Clock::now() - began).count();
        const double sync = std::chrono::duration<double, std::milli>(returned - submitted).count();
        std::fprintf(stderr, "edge_hailo_conv frames=%zu sync_ms=%.4f layout_ms=%.4f\n", count, sync, total - sync);
    }
};

void compute(ggml_tensor* dst, int thread, int, void*) {
    if (thread != 0) return;
    try {
        static HailoConv engine(std::getenv("EDGE_ASR_HAILO_HEF"));
        engine.infer(dst->src[0], dst);
    } catch (const std::exception& error) {
        // GGML custom op에는 실패 반환값이 없다. 격리한 평가 자식이 잘못된 전사를 반환하지 않게 한다.
        std::fprintf(stderr, "Qwen Hailo inference failed: %s\n", error.what());
        std::abort();
    }
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
                              480, input->ne[3], &input, 1, compute, 1, nullptr);
    }
    static auto original = reinterpret_cast<decltype(&ggml_conv_2d)>(dlsym(RTLD_NEXT, "ggml_conv_2d"));
    if (!original) std::abort();
    return original(ctx, weights, input, sx, sy, px, py, dx, dy);
}
