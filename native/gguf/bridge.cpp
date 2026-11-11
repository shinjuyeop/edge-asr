// 고정한 llama.cpp/libmtmd의 CPU Qwen3-ASR 호출을 제공하는 작은 C ABI.
#include "llama.h"
#include "ggml-backend.h"
#include "mtmd.h"
#include "mtmd-helper.h"
#include "json.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>
using json = nlohmann::json;
namespace {
using Clock = std::chrono::steady_clock;
double since(Clock::time_point t) { return std::chrono::duration<double>(Clock::now()-t).count(); }
thread_local std::string error;
std::once_flag backend;
const char* commit = "7d7f0287530bc8981690f361d8c1627dbae1c99a";
struct State {
    llama_model* model = nullptr;
    llama_context* ctx = nullptr;
    mtmd_context* audio = nullptr;
    llama_sampler* sampler = nullptr;
    llama_batch_ext* batch = nullptr;
    int max_new = 128;
    std::string result;
    json configuration;
    ~State() {
        if (batch) llama_batch_ext_free(batch);
        if (sampler) llama_sampler_free(sampler);
        if (audio) mtmd_free(audio);
        if (ctx) llama_free(ctx);
        if (model) llama_model_free(model);
    }
};
std::string hex(const std::string& value) {
    const char* digits = "0123456789abcdef"; std::string out;
    for (unsigned char c : value) { out += digits[c>>4]; out += digits[c&15]; }
    return out;
}
bool repetition(const std::vector<llama_token>& ids) {
    if (ids.size()<64) return false;
    std::unordered_set<llama_token> unique(ids.end()-64,ids.end());
    return unique.size()<=3;
}
std::string piece(const llama_vocab* vocab,llama_token token) {
    std::vector<char> buffer(256);
    int n=llama_token_to_piece(vocab,token,buffer.data(),buffer.size(),0,false);
    if (n<0) { buffer.resize(-n); n=llama_token_to_piece(vocab,token,buffer.data(),buffer.size(),0,false); }
    if (n<0) throw std::runtime_error("token_to_piece failed");
    return std::string(buffer.data(),n);
}
}
extern "C" {
__attribute__((visibility("default"))) int edge_gguf_abi() { return 1; }
__attribute__((visibility("default"))) const char* edge_gguf_commit() { return commit; }
__attribute__((visibility("default"))) const char* edge_gguf_error() { return error.c_str(); }
__attribute__((visibility("default"))) void* edge_gguf_create_threads(const char* model,const char* mmproj,int threads,int batch_threads,int context,int max_new) {
    try {
        error.clear();
        if (threads<1 || batch_threads<1 || context!=512 || max_new<1 || max_new>256) throw std::runtime_error("invalid fixed CPU configuration");
        std::call_once(backend, []{ ggml_backend_load_all(); llama_backend_init(); });
        auto s=std::make_unique<State>(); s->max_new=max_new;
        auto mp=llama_model_default_params(); mp.n_gpu_layers=0;
        s->model=llama_model_load_from_file(model,mp);
        if (!s->model) throw std::runtime_error("model load failed");
        auto cp=llama_context_default_params(); cp.n_ctx=context; cp.n_batch=context; cp.n_ubatch=context;
        cp.n_threads=threads; cp.n_threads_batch=batch_threads; cp.no_perf=false;
        s->ctx=llama_init_from_model(s->model,cp);
        if (!s->ctx) throw std::runtime_error("context creation failed");
        auto ap=mtmd_context_params_default(); ap.use_gpu=false; ap.n_threads=batch_threads; ap.warmup=false; ap.print_timings=false;
        s->audio=mtmd_init_from_file(mmproj,s->model,ap);
        if (!s->audio || !mtmd_support_audio(s->audio) || mtmd_get_audio_sample_rate(s->audio)!=16000)
            throw std::runtime_error("expected 16000Hz audio projector");
        s->sampler=llama_sampler_init_greedy(); s->batch=llama_batch_ext_init(s->ctx);
        if (!s->sampler || !s->batch) throw std::runtime_error("sampler/batch creation failed");
        s->configuration={{"context",llama_n_ctx(s->ctx)},{"n_batch",llama_n_batch(s->ctx)},{"n_ubatch",llama_n_ubatch(s->ctx)},
                          {"threads",llama_n_threads(s->ctx)},{"threads_batch",llama_n_threads_batch(s->ctx)},{"audio_threads",ap.n_threads},
                          {"cache_type_k",ggml_type_name(cp.type_k)},{"cache_type_v",ggml_type_name(cp.type_v)},
                          {"flash_attention_setting",int(cp.flash_attn_type)},{"audio_sample_rate",mtmd_get_audio_sample_rate(s->audio)},
                          {"system_info",llama_print_system_info()},{"llama_version",llama_version()}};
        return s.release();
    } catch (const std::exception& e) { error=e.what(); return nullptr; }
}
__attribute__((visibility("default"))) void* edge_gguf_create(const char* model,const char* mmproj,int threads,int context,int max_new) {
    return edge_gguf_create_threads(model,mmproj,threads,threads,context,max_new);
}
// 입력은 같은 batch PCM이다. callback은 토큰 바이트 전달만 하며 0 외 반환은 호출 실패로 남긴다.
typedef int (*edge_token_callback)(void*,const void*,size_t,int,double);
__attribute__((visibility("default"))) const char* edge_gguf_transcribe_tokens(void* handle,const float* samples,size_t count,edge_token_callback callback,void* user) {
    auto* s=static_cast<State*>(handle); if (!s) return nullptr;
    json out={{"schema_version",1},{"status","failed"},{"input_samples",count},{"token_ids",json::array()},
              {"text_utf8_hex",""},{"termination","error"},{"first_generated_token_s",nullptr},{"first_visible_text_s",nullptr}};
    out["runtime_configuration"]=s->configuration;
    std::vector<llama_token> ids; std::string text;
    double callback_s=0;
    const auto began=Clock::now();
    try {
        if (!samples || count<320) throw std::runtime_error("at least 2 feature frames required");
        auto stage=Clock::now();
        llama_memory_clear(llama_get_memory(s->ctx),true); llama_sampler_reset(s->sampler); llama_perf_context_reset(s->ctx);
        out["kv_reset_s"]=since(stage);
        stage=Clock::now();
        std::unique_ptr<mtmd_bitmap,decltype(&mtmd_bitmap_free)> bitmap(mtmd_bitmap_init_from_audio(count,samples),mtmd_bitmap_free);
        std::unique_ptr<mtmd_input_chunks,decltype(&mtmd_input_chunks_free)> chunks(mtmd_input_chunks_init(),mtmd_input_chunks_free);
        if (!bitmap || !chunks) throw std::runtime_error("audio/chunk allocation failed");
        // mtmd가 marker에 audio_start/audio_end를 붙인다. 외부에서 중복 추가하지 않는다.
        std::string prompt="<|im_start|>system\n<|im_end|>\n<|im_start|>user\n";
        prompt+=mtmd_default_marker();
        prompt+="<|im_end|>\n<|im_start|>assistant\nlanguage Korean<asr_text>";
        mtmd_input_text input{prompt.c_str(),prompt.size(),false,true};
        const mtmd_bitmap* bitmaps[]={bitmap.get()};
        if (mtmd_tokenize(s->audio,chunks.get(),&input,bitmaps,1)!=0) throw std::runtime_error("audio preprocessing/tokenization failed");
        const auto positions=mtmd_helper_get_n_pos(chunks.get());
        out["prompt_positions"]=positions; out["prompt_tokens"]=mtmd_helper_get_n_tokens(chunks.get());
        size_t audio_tokens=0;
        for(size_t i=0;i<mtmd_input_chunks_size(chunks.get());++i) {
            const auto* c=mtmd_input_chunks_get(chunks.get(),i);
            if(mtmd_input_chunk_get_type(c)==MTMD_INPUT_CHUNK_TYPE_AUDIO) audio_tokens+=mtmd_input_chunk_get_n_tokens(c);
        }
        out["audio_tokens"]=audio_tokens; out["preprocess_s"]=since(stage);
        if (positions+s->max_new+1>int(llama_n_ctx(s->ctx))) { out["termination"]="context_limit"; throw std::runtime_error("full input plus output reservation exceeds context; no truncation"); }
        stage=Clock::now(); llama_pos past=0;
        if(mtmd_helper_eval_chunks(s->audio,s->ctx,chunks.get(),0,0,llama_n_batch(s->ctx),true,&past)!=0)
            throw std::runtime_error("audio encoder/prefill failed");
        out["audio_and_prefill_s"]=since(stage);
        double decode_s=0,sample_s=0;int decode_calls=0;
        const auto* vocab=llama_model_get_vocab(s->model);
        for(int i=0;i<s->max_new;++i) {
            stage=Clock::now();llama_token token=llama_sampler_sample(s->sampler,s->ctx,-1);llama_sampler_accept(s->sampler,token);sample_s+=since(stage);
            if(llama_vocab_is_eog(vocab,token)) { out["termination"]="eog";out["termination_token_id"]=token;break; }
            if(ids.empty()) out["first_generated_token_s"]=since(began);
            ids.push_back(token);const auto bytes=piece(vocab,token);text+=bytes;
            if(callback) {
                stage=Clock::now();
                const int status=callback(user,bytes.data(),bytes.size(),int(ids.size()),since(began));
                callback_s+=since(stage);
                if(status!=0) { out["termination"]="callback_error";throw std::runtime_error("token callback failed"); }
            }
            if(i>0 && repetition(ids)) { out["termination"]="repetition";break; }
            if(i+1==s->max_new) { out["termination"]="token_limit";break; }
            if(past>=int(llama_n_ctx(s->ctx))) { out["termination"]="context_limit";break; }
            stage=Clock::now();llama_batch_ext_clear(s->batch);
            int idx=llama_batch_ext_add_token(s->batch,0,token);
            if(idx<0 || !llama_batch_ext_set_pos(s->batch,idx,&past) || !llama_batch_ext_set_output_logits(s->batch,idx,true))
                throw std::runtime_error("token batch preparation failed");
            if(llama_process(s->ctx,LLAMA_PROCESS_TYPE_DECODE,s->batch)!=0) throw std::runtime_error("token decode failed");
            ++past;++decode_calls;decode_s+=since(stage);
        }
        out["token_decode_s"]=decode_s;out["token_decode_calls"]=decode_calls;out["sampling_s"]=sample_s;
        if(out["termination"]=="eog") out["status"]="success";
        else out["error"]="generation did not terminate with eog";
    } catch (const std::exception& e) { out["error"]=e.what(); }
    out["token_ids"]=ids;out["text_utf8_hex"]=hex(text);out["native_elapsed_s"]=since(began);
    out["token_callback_enabled"]=callback!=nullptr;out["token_callback_s"]=callback_s;
    s->result=out.dump();return s->result.c_str();
}
__attribute__((visibility("default"))) const char* edge_gguf_transcribe(void* handle,const float* samples,size_t count) {
    return edge_gguf_transcribe_tokens(handle,samples,count,nullptr,nullptr);
}
__attribute__((visibility("default"))) void edge_gguf_destroy(void* handle) { delete static_cast<State*>(handle); }
}
