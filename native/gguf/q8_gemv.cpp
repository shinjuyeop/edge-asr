// Q8 GEMV의 정수 누적/가중치 사전 읽기를 비교하는 선택 실험. 운영 기본에는 로딩하지 않는다.
// 원래 가중치 배치·블록별 float 누적 순서를 유지한다. 실제 속도는 별도 측정한다.
// Derived from ggml-org/llama.cpp 7d7f028, ggml-cpu/arch/arm/repack.cpp.
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

#if defined(__aarch64__) && defined(__ARM_NEON) && defined(__ARM_FEATURE_DOTPROD)
#include <arm_neon.h>
#include "repack.h"
#define EDGE_Q8_ARM 1
#else
#define EDGE_Q8_ARM 0
#endif

namespace {
using Kernel = void(*)(int, float*, size_t, const void*, const void*, int, int);
std::atomic<int> mode{0}, active{0};
std::atomic<uint64_t> replaced{0}, forwarded{0};

Kernel original() {
    static Kernel fn = [] {
        const char* path = std::getenv("EDGE_ASR_Q8_CPU");
        void* handle = path ? dlopen(path, RTLD_LAZY | RTLD_NOLOAD) : nullptr;
        auto real = handle ? reinterpret_cast<Kernel>(dlsym(handle, "ggml_gemv_q8_0_4x4_q8_0")) : nullptr;
        if (!real) {
            std::fprintf(stderr, "Q8 GEMV: loaded CPU library is required\n");
            std::abort();
        }
        dlclose(handle);
        return real;
    }();
    return fn;
}

#if EDGE_Q8_ARM
template<bool Split, int Ahead>
void gemv(int n, float* out, const void* weights, const void* input, int cols) {
    const auto* w = static_cast<const block_q8_0x4*>(weights);
    const size_t blocks = size_t(n / QK8_0) * size_t(cols / 4);
    size_t remaining = blocks;
    for (int c = 0; c < cols; c += 4) {
        const auto* x = static_cast<const block_q8_0*>(input);
        float32x4_t acc = vdupq_n_f32(0);
        for (int b = 0; b < n / QK8_0; ++b, ++w, ++x, --remaining) {
            if constexpr (Ahead > 0) {
                if (remaining > Ahead) {
                    // 주소식도 호출자가 넘긴 packed 가중치 범위 안에서만 만든다.
                    const auto* future = reinterpret_cast<const char*>(w + Ahead);
                    static_assert(sizeof(block_q8_0x4) == 136, "fixed upstream packing");
                    __builtin_prefetch(future, 0, 3);
                    __builtin_prefetch(future + 64, 0, 3);
                    __builtin_prefetch(future + 128, 0, 3);
                }
            }
            const auto lo = vld1q_s8_x4(w->qs);
            const auto hi = vld1q_s8_x4(w->qs + 64);
            const auto a = vld1q_s8_x2(x->qs);
            auto r0 = vdupq_n_s32(0);
            r0 = vdotq_laneq_s32(r0, lo.val[0], a.val[0], 0);
            r0 = vdotq_laneq_s32(r0, lo.val[1], a.val[0], 1);
            r0 = vdotq_laneq_s32(r0, lo.val[2], a.val[0], 2);
            r0 = vdotq_laneq_s32(r0, lo.val[3], a.val[0], 3);
            auto r1 = Split ? vdupq_n_s32(0) : r0;
            r1 = vdotq_laneq_s32(r1, hi.val[0], a.val[1], 0);
            r1 = vdotq_laneq_s32(r1, hi.val[1], a.val[1], 1);
            r1 = vdotq_laneq_s32(r1, hi.val[2], a.val[1], 2);
            r1 = vdotq_laneq_s32(r1, hi.val[3], a.val[1], 3);
            // 32개의 int8 곱은 int32 범위 안이다. float 변환 이전에 정확히 합친다.
            const auto sum = Split ? vaddq_s32(r0, r1) : r1;
            const auto wd = vld1_f16(reinterpret_cast<const __fp16*>(w->d));
            const auto xd = vld1_dup_f16(reinterpret_cast<const __fp16*>(&x->d));
            acc = vfmaq_f32(acc, vcvtq_f32_s32(sum), vmulq_f32(vcvt_f32_f16(xd), vcvt_f32_f16(wd)));
        }
        vst1q_f32(out + c, acc);
    }
}
#endif
}

extern "C" int edge_q8_gemv_supported() { return EDGE_Q8_ARM; }
extern "C" int edge_q8_gemv_mode() { return mode.load(); }

// 모델을 직렬 호출하는 실행기가 호출 사이에서만 제어한다.
extern "C" int edge_q8_gemv_begin(int next) {
    if ((next != 0 && next != 1) || (next == 1 && !EDGE_Q8_ARM) || active.load()) return -1;
    mode = next;
    replaced = 0;
    forwarded = 0;
    return 0;
}

// 기존 begin(0/1)은 보존한다. 0블록은 같은 컴파일 경로의 사전 읽기 없는 대조이다.
// mode 2/3/4 = 원래 정수 누적 순서 + prefetch 0/4/8블록(0/544/1088바이트).
extern "C" int edge_q8_gemv_prefetch_begin(int blocks) {
    if ((blocks!=0 && blocks!=4 && blocks!=8) || !EDGE_Q8_ARM || active.load()) return -1;
    mode = blocks==0 ? 2 : blocks==4 ? 3 : 4;
    replaced = 0;
    forwarded = 0;
    return 0;
}

extern "C" uint64_t edge_q8_gemv_calls(int replacement) {
    return replacement ? replaced.load() : forwarded.load();
}

extern "C" void ggml_gemv_q8_0_4x4_q8_0(int n, float* out, size_t stride,
                                         const void* weights, const void* input,
                                         int rows, int cols) {
    ++active;
#if EDGE_Q8_ARM
    const int selected = mode.load();
    if (selected != 0 && n > 0 && n % QK8_0 == 0 && rows == 1 && cols >= 0 && cols % 4 == 0) {
        ++replaced;
        switch (selected) {
            case 1: gemv<true, 0>(n, out, weights, input, cols); break;
            case 2: gemv<false, 0>(n, out, weights, input, cols); break;
            case 3: gemv<false, 4>(n, out, weights, input, cols); break;
            case 4: gemv<false, 8>(n, out, weights, input, cols); break;
            default: std::abort();
        }
    } else
#endif
    {
        ++forwarded;
        original()(n, out, stride, weights, input, rows, cols);
    }
    --active;
}
