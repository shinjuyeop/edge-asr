// 고정 Qwen 합성곱2/3만 원래/재컴파일6열/후보4열 SGEMM으로 대조한다.
// 원래 GGML threadpool·FP16 누적/축약을 재사용한다. 운영 기본은 원래 함수 전달이다.
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <string>
#include <time.h>
#include "ggml-cpu-impl.h"
#include "llamafile/sgemm.h"

#define ARGS const ggml_compute_params* p, int64_t m, int64_t n, int64_t k, const void* a, int64_t lda, const void* b, int64_t ldb, void* c, int64_t ldc, int at, int bt, int ct
#define PASS p, m, n, k, a, lda, b, ldb, c, ldc, at, bt, ct
#define API extern "C" __attribute__((visibility("default")))
extern "C" bool edge_conv4(ARGS);
extern "C" bool edge_conv6(ARGS);
API int edge_conv_begin(int next);
API const char* edge_conv_result();

namespace {
using Kernel = bool(*)(ARGS);
std::atomic<int> mode{0}, active{0};
struct Totals {
    std::atomic<uint64_t> calls{0}, accepted{0}, cpu_ns{0}, wall_ns{0};
};
Totals totals[2][3]; // m800/m208, worker0/1/2
std::atomic<uint64_t> other{0};
uint64_t now(clockid_t clock) {
    timespec t{};
    if (clock_gettime(clock, &t)) std::abort();
    return uint64_t(t.tv_sec)*1000000000ULL + uint64_t(t.tv_nsec);
}
Kernel original() {
    static Kernel fn = [] {
        const char* path = std::getenv("EDGE_ASR_CONV_CPU");
        void* h = path ? dlopen(path, RTLD_LAZY | RTLD_NOLOAD) : nullptr;
        auto f = h ? reinterpret_cast<Kernel>(dlsym(h, "llamafile_sgemm")) : nullptr;
        if (!f) { std::fprintf(stderr, "conv SGEMM: loaded CPU library is required\n"); std::abort(); }
        dlclose(h);
        return f;
    }();
    return fn;
}
}

// 직렬 모델 호출 사이에서만 모드/집계를 바꾼다.
API int edge_conv_begin(int next) {
    if (next < 0 || next > 2 || active.load()) return -1;
    mode = next;
    other = 0;
    for (auto& shape : totals) for (auto& t : shape) {
        t.calls = 0; t.accepted = 0; t.cpu_ns = 0; t.wall_ns = 0;
    }
    return 0;
}

API bool llamafile_sgemm(ARGS) {
    ++active;
    const bool target = p && !p->use_ref && p->nth == 3 && p->ith >= 0 && p->ith < 3 &&
        (m == 800 || m == 208) && n == 480 && k == 4320 &&
        lda == k && ldb == k && ldc == m &&
        at == GGML_TYPE_F16 && bt == GGML_TYPE_F16 && ct == GGML_TYPE_F32;
    if (!target) {
        ++other;
        const bool ok = original()(PASS);
        --active;
        return ok;
    }
    const int selected = mode.load();
    const auto wall = now(CLOCK_MONOTONIC), cpu = now(CLOCK_THREAD_CPUTIME_ID);
    const bool ok = selected == 1 ? edge_conv6(PASS) : selected == 2 ? edge_conv4(PASS) : original()(PASS);
    auto& t = totals[m == 800 ? 0 : 1][p->ith];
    t.cpu_ns += now(CLOCK_THREAD_CPUTIME_ID) - cpu;
    t.wall_ns += now(CLOCK_MONOTONIC) - wall;
    ++t.calls;
    if (ok) ++t.accepted;
    --active;
    return ok;
}

API const char* edge_conv_result() {
    if (active.load()) return nullptr;
    static std::string out;
    out = "{\"mode\":" + std::to_string(mode.load()) + ",\"other_forwarded\":" + std::to_string(other.load()) + ",\"groups\":[";
    bool first = true;
    for (int s = 0; s < 2; ++s) for (int w = 0; w < 3; ++w) {
        const auto& t = totals[s][w];
        if (!t.calls.load()) continue;
        if (!first) out += ',';
        first = false;
        out += "{\"m\":" + std::to_string(s == 0 ? 800 : 208) + ",\"n\":480,\"k\":4320,\"worker\":" + std::to_string(w) +
            ",\"calls\":" + std::to_string(t.calls.load()) + ",\"accepted\":" + std::to_string(t.accepted.load()) +
            ",\"cpu_ns\":" + std::to_string(t.cpu_ns.load()) + ",\"wall_ns\":" + std::to_string(t.wall_ns.load()) + "}";
    }
    out += "]}";
    return out.c_str();
}
