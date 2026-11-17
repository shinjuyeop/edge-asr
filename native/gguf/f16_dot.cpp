// 선택 FP16×FP32 내적 실험. 기존 64개 블록/FP32 덧셈/FP64 블록 합 순서를 보존한다.
// Derived from ggml-org/llama.cpp 7d7f028, ggml-cpu/vec.cpp and simd-mappings.h.
// MIT License
// Copyright (c) 2023-2026 The ggml authors
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>

#if defined(__aarch64__) && defined(__ARM_NEON) && defined(__ARM_FEATURE_FMA) && defined(__ARM_FP16_FORMAT_IEEE)
#include <arm_neon.h>
#define EDGE_F16_ARM 1
#else
#define EDGE_F16_ARM 0
#endif

namespace {
using Kernel = void(*)(int, float*, size_t, const uint16_t*, size_t, const float*, size_t, int);
std::atomic<int> mode{0};
std::atomic<unsigned> next_slot{0};
std::atomic<uint64_t> overflow{0};
struct alignas(64) Counts { uint64_t calls[2][4]{}; };
Counts counts[64];

Kernel original() {
    static Kernel fn = [] {
        const char* path = std::getenv("EDGE_ASR_F16_CPU");
        void* handle = path ? dlopen(path, RTLD_LAZY | RTLD_NOLOAD) : nullptr;
        auto real = handle ? reinterpret_cast<Kernel>(dlsym(handle, "ggml_vec_dot_f16_f32")) : nullptr;
        if (!real) { std::fprintf(stderr, "F16 dot: loaded CPU library is required\n"); std::abort(); }
        dlclose(handle);
        return real;
    }();
    return fn;
}

#if EDGE_F16_ARM
void fused(int n, float* out, const uint16_t* x, const float* y) {
    double total = 0.0;
    for (int i = 0; i < n; i += 64) {
        float32x4_t sums[4] = {vdupq_n_f32(0), vdupq_n_f32(0), vdupq_n_f32(0), vdupq_n_f32(0)};
        for (int j = 0; j < 64; j += 16) {
            for (int k = 0; k < 4; ++k) {
                const int offset = i + j + k * 4;
                const auto a = vcvt_f32_f16(vreinterpret_f16_u16(vld1_u16(x + offset)));
                sums[k] = vfmaq_f32(sums[k], a, vld1q_f32(y + offset));
            }
        }
        // upstream NEON reduction: (sum0 + sum2) + (sum1 + sum3).
        total += vaddvq_f32(vaddq_f32(vaddq_f32(sums[0], sums[2]), vaddq_f32(sums[1], sums[3])));
    }
    *out = static_cast<float>(total);
}
#endif
}

extern "C" int edge_f16_dot_supported() { return EDGE_F16_ARM; }

// begin/calls는 모델 호출 사이의 유휴 상태에서만 사용한다. worker별 집계로 내적마다 원자 증가를 피한다.
extern "C" int edge_f16_dot_begin(int next) {
    if ((next != 0 && next != 1) || (next == 1 && !EDGE_F16_ARM)) return -1;
    mode.store(next, std::memory_order_relaxed);
    for (auto& c : counts) c = Counts{};
    overflow = 0;
    return 0;
}
extern "C" uint64_t edge_f16_dot_calls(int replaced, int bucket) {
    if (replaced < 0 || replaced > 1 || bucket < 0 || bucket > 3) return 0;
    uint64_t total = 0;
    for (const auto& c : counts) total += c.calls[replaced][bucket];
    return total;
}
extern "C" uint64_t edge_f16_dot_overflow() { return overflow.load(); }

extern "C" void ggml_vec_dot_f16_f32(int n, float* s, size_t bs, const uint16_t* x, size_t bx, const float* y, size_t by, int nrc) {
    const bool replace = EDGE_F16_ARM && mode.load(std::memory_order_relaxed) == 1 && n > 0 && n % 64 == 0 && nrc == 1;
    thread_local const unsigned slot = next_slot.fetch_add(1);
    if (slot < 64) ++counts[slot].calls[replace][n == 9 ? 0 : n == 4320 ? 1 : n == 7680 ? 2 : 3];
    else ++overflow;
#if EDGE_F16_ARM
    if (replace) { fused(n, s, x, y); return; }
#endif
    original()(n, s, bs, x, bx, y, by, nrc);
}
