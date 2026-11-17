// 고정 GGML CPU 라이브러리용 선택 LD_PRELOAD 진단. 운영 bridge/엔진은 수정하지 않는다.
// 시간은 작업자별 합계이며 병렬 wall time과 다르다. 커널 내부 unpack/dot은 분리하지 않는다.
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <string>
#include <time.h>
#include "perf_counters.h"
#include "llama.h"
#include "mtmd.h"
#include "mtmd-helper.h"

namespace {
enum Stage { idle, text_prefix, audio_encoder, audio_prefill, text_suffix, token_decode, stages };
const char* stage_names[] = {"idle", "text_prefix", "audio_encoder", "audio_prefill", "text_suffix", "token_decode"};
enum Function {
    q4k_gemm4, q4k_gemm8, q6k_gemm4, q6k_gemm8, q8_gemm4, q8_gemm8,
    q4k_gemv4, q4k_gemv8, q6k_gemv4, q6k_gemv8, q8_gemv4, q8_gemv8,
    q8_output4, q8_output8, quant4_q8, quant8_q8, quant4_q8k, quant8_q8k, row_q8, row_q8k, functions
};
const char* function_names[] = {
    "ggml_gemm_q4_K_8x4_q8_K", "ggml_gemm_q4_K_8x8_q8_K",
    "ggml_gemm_q6_K_8x4_q8_K", "ggml_gemm_q6_K_8x8_q8_K",
    "ggml_gemm_q8_0_4x4_q8_0", "ggml_gemm_q8_0_4x8_q8_0",
    "ggml_gemv_q4_K_8x4_q8_K", "ggml_gemv_q4_K_8x8_q8_K",
    "ggml_gemv_q6_K_8x4_q8_K", "ggml_gemv_q6_K_8x8_q8_K",
    "ggml_gemv_q8_0_4x4_q8_0", "ggml_gemv_q8_0_4x8_q8_0",
    "q8_output_gemv4", "q8_output_gemv8",
    "quantize_mat<4,Q8_0>", "quantize_mat<8,Q8_0>",
    "quantize_mat<4,Q8_K>", "quantize_mat<8,Q8_K>", "quantize_row_q8_0", "quantize_row_q8_K"
};
constexpr int max_workers=64;
struct WorkerTotals { std::atomic<uint64_t> calls{0}, cpu_ns{0}, work{0}; };
struct Totals {
    std::atomic<uint64_t> calls{0}, wall_ns{0}, cpu_ns{0}, work{0};
    std::atomic<uint64_t> valid{0}, invalid{0}, pmu_enabled{0}, pmu_running{0};
    std::atomic<int> pmu_errno{0};
    std::atomic<uint64_t> counters[edge_perf::count]{};
    WorkerTotals workers[max_workers];
};
Totals totals[stages][functions];
std::atomic<bool> enabled{false};
std::atomic<int> stage{idle}, in_flight{0};
thread_local int depth = 0;
std::atomic<int> pmu_bank{0}, pmu_type{0}, output_width{0}, output_stride{0};
std::atomic<int> worker_count{0};
std::atomic<uint64_t> worker_overflow{0};
thread_local int worker_slot=-1;
thread_local edge_perf::Group pmu;
Function matrix_function(Function f, int n, size_t stride) {
    if (output_width.load()>0 && n==output_width.load() && stride==size_t(output_stride.load())) {
        if (f==q8_gemv4) return q8_output4;
        if (f==q8_gemv8) return q8_output8;
    }
    return f;
}

uint64_t now(clockid_t clock) {
    timespec value{};
    if (clock_gettime(clock, &value)) std::abort();
    return uint64_t(value.tv_sec)*1000000000ULL + uint64_t(value.tv_nsec);
}
void* resolve(const char* library_env, const char* symbol) {
    const char* path = std::getenv(library_env);
    void* handle = path ? dlopen(path, RTLD_LAZY | RTLD_NOLOAD) : nullptr;
    void* result = handle ? dlsym(handle, symbol) : nullptr;
    if (!result) {
        std::fprintf(stderr, "edge kernel trace: cannot resolve %s via %s\n", symbol, library_env);
        std::abort();
    }
    dlclose(handle); // 실제 라이브러리의 소유권은 bridge에 있다.
    return result;
}
struct Measure {
    bool active;
    int phase;
    Function fn;
    uint64_t work, wall=0, cpu=0;
    int bank=0;
    bool sampled=false;
    edge_perf::Sample before{};
    Measure(Function f, uint64_t w): active(enabled.load() && depth==0), phase(stage.load()), fn(f), work(w) {
        if (depth==0) pmu.configure(enabled.load() ? pmu_bank.load() : 0, pmu_type.load());
        ++depth;
        if (active) {
            ++in_flight;
            if (worker_slot<0) worker_slot=worker_count.fetch_add(1);
            bank=pmu_bank.load();
            if (bank) sampled=pmu.read(before);
            wall=now(CLOCK_MONOTONIC); cpu=now(CLOCK_THREAD_CPUTIME_ID);
        }
    }
    ~Measure() {
        if (active) {
            const auto elapsed_cpu=now(CLOCK_THREAD_CPUTIME_ID)-cpu;
            const auto elapsed_wall=now(CLOCK_MONOTONIC)-wall;
            auto& t=totals[phase][fn];
            edge_perf::Sample after{}, delta{};
            if (bank) {
                if (sampled && pmu.read(after) && edge_perf::difference(before,after,delta)) {
                    ++t.valid; t.pmu_enabled+=delta.enabled; t.pmu_running+=delta.running;
                    for (int i=0;i<edge_perf::count;++i) t.counters[i]+=delta.values[i];
                } else { ++t.invalid; t.pmu_errno=pmu.error() ? pmu.error() : EIO; }
            }
            ++t.calls; t.wall_ns+=elapsed_wall; t.cpu_ns+=elapsed_cpu; t.work+=work;
            if (worker_slot<max_workers) {
                auto& w=t.workers[worker_slot]; ++w.calls; w.cpu_ns+=elapsed_cpu; w.work+=work;
            } else ++worker_overflow;
            --in_flight;
        }
        --depth;
    }
};
struct Phase {
    int previous;
    explicit Phase(Stage next): previous(stage.exchange(next)) {}
    ~Phase() { stage=previous; }
};
}

// PMU bank 0=끔, 1=캐시, 2=stall/bus. type은 실제 PMU sysfs에서 읽는다.
extern "C" int edge_kernel_trace_set_pmu(int bank, int type) {
    if (bank<0 || bank>2 || type<0 || stage.load()!=idle || in_flight.load()) return -1;
    pmu_bank=bank; pmu_type=type; return 0;
}
// 관측한 모델 shape만 지정한다. (0,0)은 분류 끔이며 tensor 이름을 추측하지 않는다.
extern "C" int edge_kernel_trace_set_output_shape(int width, int stride) {
    if (width<0 || stride<0 || (width==0)!=(stride==0) || stage.load()!=idle || in_flight.load()) return -1;
    output_width=width; output_stride=stride; return 0;
}

// 직렬 adapter 호출 사이에서만 제어한다. 실행 중 reset/모드 변경을 거부한다.
extern "C" int edge_kernel_trace_begin(int on) {
    if ((on!=0 && on!=1) || stage.load()!=idle || in_flight.load()) return -1;
    enabled=false;
    worker_overflow=0;
    for (auto& row:totals) for (auto& t:row) {
        t.calls=0; t.wall_ns=0; t.cpu_ns=0; t.work=0;
        t.valid=0; t.invalid=0; t.pmu_enabled=0; t.pmu_running=0; t.pmu_errno=0;
        for (auto& c:t.counters) c=0;
        for (auto& w:t.workers) { w.calls=0; w.cpu_ns=0; w.work=0; }
    }
    enabled=on==1;
    return 0;
}
extern "C" const char* edge_kernel_trace_result() {
    static std::string result;
    if (stage.load()!=idle || in_flight.load()) return nullptr;
    result="{\"schema_version\":1,\"enabled\":";
    result+=enabled.load() ? "true" : "false";
    result+=",\"pmu_bank\":"+std::to_string(pmu_bank.load());
    result+=",\"pmu_type\":"+std::to_string(pmu_type.load());
    result+=",\"output_width\":"+std::to_string(output_width.load());
    result+=",\"output_stride\":"+std::to_string(output_stride.load());
    result+=",\"worker_overflow_calls\":"+std::to_string(worker_overflow.load());
    result+=",\"groups\":[";
    bool comma=false;
    for (int s=0;s<stages;++s) for (int f=0;f<functions;++f) {
        const auto& t=totals[s][f];
        if (!t.calls.load()) continue;
        char row[640];
        std::snprintf(row,sizeof(row),"%s{\"stage\":\"%s\",\"function\":\"%s\",\"calls\":%llu,\"worker_wall_ns\":%llu,\"worker_cpu_ns\":%llu,\"work\":%llu,\"work_unit\":\"%s\"}",
            comma ? "," : "",stage_names[s],function_names[f],
            (unsigned long long)t.calls.load(),(unsigned long long)t.wall_ns.load(),
            (unsigned long long)t.cpu_ns.load(),(unsigned long long)t.work.load(),f<quant4_q8 ? "MAC" : "input_elements");
        result+=row; result.pop_back();
        if (pmu_bank.load()) {
            result+=",\"pmu\":{\"valid_calls\":"+std::to_string(t.valid.load());
            result+=",\"invalid_calls\":"+std::to_string(t.invalid.load());
            result+=",\"errno\":"+std::to_string(t.pmu_errno.load());
            result+=",\"time_enabled_ns\":"+std::to_string(t.pmu_enabled.load());
            result+=",\"time_running_ns\":"+std::to_string(t.pmu_running.load());
            for (int i=0;i<edge_perf::count;++i) {
                result+=",\""; result+=edge_perf::names[pmu_bank.load()-1][i];
                result+="\":"+std::to_string(t.counters[i].load());
            }
            result+="}";
        }
        result+=",\"workers\":[";
        bool worker_comma=false;
        for (int i=0;i<max_workers;++i) {
            const auto& w=t.workers[i]; if (!w.calls.load()) continue;
            if (worker_comma) result+=",";
            result+="{\"slot\":"+std::to_string(i)+",\"calls\":"+std::to_string(w.calls.load());
            result+=",\"cpu_ns\":"+std::to_string(w.cpu_ns.load())+",\"work\":"+std::to_string(w.work.load())+"}";
            worker_comma=true;
        }
        result+="]}"; comma=true;
    }
    result+="]}";
    return result.c_str();
}

#define MATRIX(NAME, ID) \
extern "C" void NAME(int n, float* out, size_t stride, const void* a, const void* b, int rows, int cols) { \
    using Fn=void(*)(int,float*,size_t,const void*,const void*,int,int); \
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_CPU",#NAME)); \
    Measure measured(matrix_function(ID,n,stride),uint64_t(n)*uint64_t(rows)*uint64_t(cols)); \
    real(n,out,stride,a,b,rows,cols); \
}
MATRIX(ggml_gemm_q4_K_8x4_q8_K,q4k_gemm4)
MATRIX(ggml_gemm_q4_K_8x8_q8_K,q4k_gemm8)
MATRIX(ggml_gemm_q6_K_8x4_q8_K,q6k_gemm4)
MATRIX(ggml_gemm_q6_K_8x8_q8_K,q6k_gemm8)
MATRIX(ggml_gemm_q8_0_4x4_q8_0,q8_gemm4)
MATRIX(ggml_gemm_q8_0_4x8_q8_0,q8_gemm8)
MATRIX(ggml_gemv_q4_K_8x4_q8_K,q4k_gemv4)
MATRIX(ggml_gemv_q4_K_8x8_q8_K,q4k_gemv8)
MATRIX(ggml_gemv_q6_K_8x4_q8_K,q6k_gemv4)
MATRIX(ggml_gemv_q6_K_8x8_q8_K,q6k_gemv8)
MATRIX(ggml_gemv_q8_0_4x4_q8_0,q8_gemv4)
MATRIX(ggml_gemv_q8_0_4x8_q8_0,q8_gemv8)
#undef MATRIX

// 고정 upstream commit의 C++ template 심볼. 수치 계산은 원래 함수로 전달한다.
#define QUANT(NAME, SYMBOL, ID) \
extern "C" void NAME(const float*,void*,int64_t,int64_t) __asm__(SYMBOL); \
extern "C" void NAME(const float* x,void* y,int64_t rows,int64_t k) { \
    using Fn=void(*)(const float*,void*,int64_t,int64_t); \
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_CPU",SYMBOL)); \
    Measure measured(ID,uint64_t(rows)*uint64_t(k)); real(x,y,rows,k); \
}
QUANT(quant4q8,"_Z19ggml_quantize_mat_tILl4EL9ggml_type8EEvPKfPvll",quant4_q8)
QUANT(quant8q8,"_Z19ggml_quantize_mat_tILl8EL9ggml_type8EEvPKfPvll",quant8_q8)
QUANT(quant4q8k,"_Z19ggml_quantize_mat_tILl4EL9ggml_type15EEvPKfPvll",quant4_q8k)
QUANT(quant8q8k,"_Z19ggml_quantize_mat_tILl8EL9ggml_type15EEvPKfPvll",quant8_q8k)
#undef QUANT
#define ROW(NAME, ID) \
extern "C" void NAME(const float* x,void* y,int64_t k) { \
    using Fn=void(*)(const float*,void*,int64_t); \
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_CPU",#NAME)); \
    Measure measured(ID,uint64_t(k)); real(x,y,k); \
}
ROW(quantize_row_q8_0,row_q8)
ROW(quantize_row_q8_K,row_q8k)
#undef ROW

// 공개 ABI를 고정 upstream 헤더로 검사한다. opaque 포인터는 역참조하지 않는다.
extern "C" int32_t mtmd_encode_chunk(mtmd_context* ctx,const mtmd_input_chunk* chunk) {
    using Fn=decltype(&mtmd_encode_chunk);
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_MTMD","mtmd_encode_chunk"));
    Phase p(audio_encoder); return real(ctx,chunk);
}
extern "C" int32_t mtmd_helper_decode_image_chunk(mtmd_context* ctx,llama_context* lctx,const mtmd_input_chunk* chunk,float* embd,
    llama_pos past,llama_seq_id seq,int32_t batch,llama_pos* next,mtmd_helper_post_decode_callback callback,void* user) {
    using Fn=decltype(&mtmd_helper_decode_image_chunk);
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_MTMD","mtmd_helper_decode_image_chunk"));
    Phase p(audio_prefill); return real(ctx,lctx,chunk,embd,past,seq,batch,next,callback,user);
}
extern "C" int32_t mtmd_helper_eval_chunk_single(mtmd_context* ctx,llama_context* lctx,const mtmd_input_chunk* chunk,
    llama_pos past,llama_seq_id seq,int32_t batch,bool logits,llama_pos* next) {
    using Fn=decltype(&mtmd_helper_eval_chunk_single);
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_MTMD","mtmd_helper_eval_chunk_single"));
    Phase p(past==0 ? text_prefix : text_suffix); return real(ctx,lctx,chunk,past,seq,batch,logits,next);
}
extern "C" int32_t llama_process(llama_context* ctx,llama_process_type type,llama_batch_ext* batch) {
    using Fn=decltype(&llama_process);
    static auto real=reinterpret_cast<Fn>(resolve("EDGE_ASR_TRACE_LLAMA","llama_process"));
    Phase p(stage.load()==idle ? token_decode : Stage(stage.load())); return real(ctx,type,batch);
}
