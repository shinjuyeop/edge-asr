// 선택 preload. 기존 log-Mel 함수의 같은 연산으로 새 프레임만 계산하고 전체 정규화는 원래 호출자에 맡긴다.
#include "mtmd-audio.h"
#include "mel_prefix_cache.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <string>

#define API extern "C" __attribute__((visibility("default")))
namespace {
using Fn = bool(*)(const mtmd_audio_mel_config&,const float*,const float*,int64_t,float*);
std::atomic<int> active{0};
int mode = 0, calls = 0, reused = 0, computed = 0, eligible = 0;
bool equal = true;
double elapsed = 0;
MelPrefixCache cache;
Fn original() {
    static Fn fn = [] {
        const char* path = std::getenv("EDGE_ASR_MEL_MTMD");
        void* h = path ? dlopen(path, RTLD_LAZY|RTLD_NOLOAD) : nullptr;
        auto f = h ? reinterpret_cast<Fn>(dlsym(h, "_Z18mtmd_audio_log_melRK21mtmd_audio_mel_configPKfS3_lPf")) : nullptr;
        if (!f) { std::fprintf(stderr,"mel cache: verified original mtmd library required\n"); std::abort(); }
        dlclose(h); return f;
    }();
    return fn;
}
}
API int edge_mel_begin(int next, int reset) {
    if (active.load() || next<0 || next>2) return -1;
    mode=next; calls=reused=computed=eligible=0; equal=true; elapsed=0;
    if (reset || !mode) cache.reset();
    return 0;
}
API int edge_mel_release() { if(active.load())return -1; mode=0; cache.release(); return 0; }
API const char* edge_mel_result() {
    if(active.load())return nullptr;
    static std::string result;
    result="{\"mode\":"+std::to_string(mode)+",\"calls\":"+std::to_string(calls)+
      ",\"eligible_calls\":"+std::to_string(eligible)+",\"reused_frames\":"+std::to_string(reused)+",\"computed_frames\":"+std::to_string(computed)+
      ",\"equivalent\":"+(equal?"true":"false")+",\"elapsed_s\":"+std::to_string(elapsed)+
      ",\"cache_capacity_bytes\":"+std::to_string(cache.bytes())+"}";
    return result.c_str();
}

__attribute__((visibility("default"))) bool mtmd_audio_log_mel(const mtmd_audio_mel_config& c,
    const float* samples,const float* window,int64_t frames,float* out) {
    if (++active != 1) { std::fprintf(stderr,"mel cache: concurrent model execution forbidden\n"); std::abort(); }
    struct Guard { ~Guard() { --active; } } guard;
    const auto began=std::chrono::steady_clock::now();
    auto compute=[&](const float* s,int64_t n,float* o){return original()(c,s,window,n,o);};
    bool ok;
    const bool target=c.n_mel==128 && c.n_fft_bins==201 && c.hop_length==160 && c.hann_window_size==400 &&
        c.sample_rate==16000 && c.no_padding && !c.center_padding && !c.preemph && !c.use_natural_log &&
        !c.norm_per_feature && !c.use_magnitude && !c.mel_floor_add && c.cache &&
        c.cache->filters.data.size()==128*201 && c.cache->cos_vals.size()==400 && c.cache->sin_vals.size()==400;
    if (mode && target && frames<=MelPrefixCache::max_frames && frames>0) {
        ++eligible;
        std::vector<float> key(window,window+400);
        for (const auto* values : {&c.cache->filters.data,&c.cache->cos_vals,&c.cache->sin_vals})
            key.insert(key.end(),values->begin(),values->end());
        key.push_back(c.mel_floor); key.push_back(float(c.n_threads));
        ok=cache.run(samples,int(frames),key,out,mode,compute);
        reused+=cache.reused; computed+=cache.computed; equal &= cache.equivalent;
    } else { cache.reset(); ok=compute(samples,frames,out); computed+=int(frames); }
    ++calls; elapsed+=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
    return ok;
}
