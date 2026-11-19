// 고정한 llama.cpp/libmtmd의 CPU Qwen3-ASR 호출을 제공하는 작은 C ABI.
#include "llama.h"
#include "ggml-backend.h"
#include "mtmd.h"
#include "mtmd-helper.h"
#include "prefix_cache.h"
#include "audio_window_cache.h"
#include "token_draft.h"
#ifdef EDGE_ASR_TRACE_TOKENS
#include "token_trace.h"
#endif
#ifdef EDGE_ASR_PROFILE_OPERATORS
#include "operator_profile.h"
#endif
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
    TextPrefixCache prefix_cache;
    AudioWindowCache audio_windows;
    int audio_window_ms = 0;
    bool audio_window_reuse = false, audio_window_kv_valid = false;
    std::vector<int32_t> pending_token_draft;
    int token_draft_block = 1;
#ifdef EDGE_ASR_TRACE_TOKENS
    bool pending_token_trace = false;
#endif
#ifdef EDGE_ASR_PROFILE_OPERATORS
    OperatorProfile operators;
#endif
    ~State() {
        if (batch) llama_batch_ext_free(batch);
        if (sampler) llama_sampler_free(sampler);
        if (audio) mtmd_free(audio);
        if (ctx) llama_free(ctx);
        if (model) llama_model_free(model);
    }
};
#ifdef EDGE_ASR_PROFILE_OPERATORS
uint64_t profile_now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}
bool profile_callback(ggml_tensor* t, bool ask, void* user) {
    auto& profile=*static_cast<OperatorProfile*>(user);
    if (!profile.enabled) return !ask;
    if (ask) { profile.before(t,profile_now()); return true; }
    const auto ended=profile_now();
    OperatorNode node; node.name=t->name; node.op=ggml_op_name(t->op);
    std::copy_n(t->ne,4,node.shape.begin());
    if (t->src[0]) {
        node.lhs_name=t->src[0]->name; node.lhs_type=ggml_type_name(t->src[0]->type);
        std::copy_n(t->src[0]->ne,4,node.lhs_shape.begin());
    }
    if (t->src[1]) {
        node.rhs_type=ggml_type_name(t->src[1]->type);
        std::copy_n(t->src[1]->ne,4,node.rhs_shape.begin());
    }
    profile.after(t,ended,node); return true;
}
json profile_result(const OperatorProfile& p) {
    json groups=json::array();
    for (const auto& entry:p.groups) {
        const auto& a=entry.second; const auto& n=a.node;
        groups.push_back({{"stage",entry.first.first},{"name",n.name},{"op",n.op},{"lhs_name",n.lhs_name},
            {"lhs_type",n.lhs_type},{"rhs_type",n.rhs_type},{"shape",n.shape},{"lhs_shape",n.lhs_shape},
            {"rhs_shape",n.rhs_shape},{"shape_changed",a.shape_changed},{"count",a.count},
            {"total_s",a.total_ns/1e9},{"max_s",a.max_ns/1e9}});
    }
    return {{"schema_version",1},{"enabled",p.enabled},{"valid",p.complete()},
        {"overflow_groups",p.overflow},{"groups",groups},
        {"scope","per-node synchronized graph views; fusion/scheduling may change; observer time is not production latency"}};
}
#endif
void operator_phase(State* s,const char* phase) {
#ifdef EDGE_ASR_PROFILE_OPERATORS
    s->operators.phase=phase;
#else
    (void)s; (void)phase;
#endif
}
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

// upstream과 같은 chunk 순서·평가 함수를 사용하고 시간만 세분한다.
llama_pos eval_prompt(State* s, const mtmd_input_chunks* chunks, bool hit, size_t prefix_size, json& out,
                      const std::vector<float>& window_embeds, size_t reused_audio) {
    llama_pos past=hit ? llama_pos(prefix_size) : 0;
    for (size_t i=0;i<mtmd_input_chunks_size(chunks);++i) {
        const auto* chunk=mtmd_input_chunks_get(chunks,i);
        const auto type=mtmd_input_chunk_get_type(chunk);
        if (i==0 && hit) continue;
        auto stage=Clock::now();
        if (type==MTMD_INPUT_CHUNK_TYPE_TEXT) {
            operator_phase(s,i==0 ? "text_prefix" : "text_suffix");
            const int rc=mtmd_helper_eval_chunk_single(s->audio,s->ctx,chunk,past,0,llama_n_batch(s->ctx),
                i+1==mtmd_input_chunks_size(chunks),&past);
            const char* key=i==0 ? "prefix_eval_s" : "suffix_eval_s";
            out[key]=out[key].get<double>()+since(stage);
            if (rc!=0) throw std::runtime_error("text prefill failed");
        } else if (type==MTMD_INPUT_CHUNK_TYPE_AUDIO) {
            if (s->audio_window_ms) {
                const size_t dim=llama_model_n_embd_inp(s->model), rows=window_embeds.size()/dim;
                if (mtmd_decode_use_non_causal(s->audio,chunk))
                    throw std::runtime_error("partial window requires causal attention");
                past+=llama_pos(reused_audio);
                llama_batch_ext_clear(s->batch);
                for(size_t row=reused_audio;row<rows;++row) {
                    const int idx=llama_batch_ext_add_embd(s->batch,0,{window_embeds.data()+row*dim,1,dim});
                    // mtmd_helper의 audio M-RoPE와 동일하게 네 축 모두 같은 위치를 쓴다.
                    const llama_pos pos[4]={past,past,past,past};
                    if(idx<0 || !llama_batch_ext_set_pos(s->batch,idx,pos))
                        throw std::runtime_error("window embedding batch failed");
                    ++past;
                }
                operator_phase(s,"audio_prefill");
                if(rows>reused_audio && llama_process(s->ctx,LLAMA_PROCESS_TYPE_DECODE,s->batch)!=0)
                    throw std::runtime_error("window embedding prefill failed");
                out["audio_embedding_eval_s"]=out["audio_embedding_eval_s"].get<double>()+since(stage);
                continue;
            }
            operator_phase(s,"audio_encoder");
            const int encoded=mtmd_encode_chunk(s->audio,chunk);
            out["audio_encode_s"]=out["audio_encode_s"].get<double>()+since(stage);
            if (encoded!=0) throw std::runtime_error("audio encoder failed");
            stage=Clock::now();
            operator_phase(s,"audio_prefill");
            const int decoded=mtmd_helper_decode_image_chunk(s->audio,s->ctx,chunk,mtmd_get_output_embd(s->audio),
                past,0,llama_n_batch(s->ctx),&past,nullptr,nullptr);
            out["audio_embedding_eval_s"]=out["audio_embedding_eval_s"].get<double>()+since(stage);
            if (decoded!=0) throw std::runtime_error("audio embedding prefill failed");
        } else {
            throw std::runtime_error("unexpected non-audio chunk");
        }
    }
    return past;
}

// 같은 mtmd 전처리/인코더를 창별로 호출한다. 창 경계 정규화·attention이 달라지는 실험이다.
std::vector<float> encode_audio_window(State* s,const float* pcm,size_t count,bool completed) {
    std::unique_ptr<mtmd_bitmap,decltype(&mtmd_bitmap_free)> bitmap(mtmd_bitmap_init_from_audio(count,pcm),mtmd_bitmap_free);
    std::unique_ptr<mtmd_input_chunks,decltype(&mtmd_input_chunks_free)> chunks(mtmd_input_chunks_init(),mtmd_input_chunks_free);
    const auto marker=mtmd_default_marker();
    mtmd_input_text input{marker,std::strlen(marker),false,true};
    const mtmd_bitmap* bitmaps[]={bitmap.get()};
    if(!bitmap || !chunks || mtmd_tokenize(s->audio,chunks.get(),&input,bitmaps,1)!=0)
        throw std::runtime_error("window preprocessing failed");
    const size_t dim=llama_model_n_embd_inp(s->model);
    std::vector<float> output;
    for(size_t i=0;i<mtmd_input_chunks_size(chunks.get());++i) {
        auto* chunk=mtmd_input_chunks_get(chunks.get(),i);
        if(mtmd_input_chunk_get_type(chunk)!=MTMD_INPUT_CHUNK_TYPE_AUDIO) continue;
        if(mtmd_encode_chunk(s->audio,chunk)!=0) throw std::runtime_error("window encoder failed");
        auto* values=mtmd_get_output_embd(s->audio);
        const size_t n=mtmd_input_chunk_get_n_tokens(chunk)*dim;
        output.insert(output.end(),values,values+n);
    }
    // 정확한 초 경계에는 STFT 한 프레임 때문에 패딩 sub-chunk가 생긴다.
    // 완료된 창에서는 실제 1초당13행만 남긴다. 미완료 꼬리는 원래 출력을 유지한다.
    if(completed) {
        const size_t expected=((count/160+99)/100)*13*dim;
        if(output.size()<expected) throw std::runtime_error("short completed window");
        output.resize(expected);
    }
    return output;
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
#ifdef EDGE_ASR_PROFILE_OPERATORS
        cp.cb_eval=profile_callback; cp.cb_eval_user_data=&s->operators;
#endif
        s->ctx=llama_init_from_model(s->model,cp);
        if (!s->ctx) throw std::runtime_error("context creation failed");
        auto ap=mtmd_context_params_default(); ap.use_gpu=false; ap.n_threads=batch_threads; ap.warmup=false; ap.print_timings=false;
#ifdef EDGE_ASR_PROFILE_OPERATORS
        ap.cb_eval=profile_callback; ap.cb_eval_user_data=&s->operators;
#endif
        s->audio=mtmd_init_from_file(mmproj,s->model,ap);
        if (!s->audio || !mtmd_support_audio(s->audio) || mtmd_get_audio_sample_rate(s->audio)!=16000)
            throw std::runtime_error("expected 16000Hz audio projector");
        s->sampler=llama_sampler_init_greedy(); s->batch=llama_batch_ext_init(s->ctx);
        if (!s->sampler || !s->batch) throw std::runtime_error("sampler/batch creation failed");
        s->configuration={{"context",llama_n_ctx(s->ctx)},{"n_batch",llama_n_batch(s->ctx)},{"n_ubatch",llama_n_ubatch(s->ctx)},
                          {"threads",llama_n_threads(s->ctx)},{"threads_batch",llama_n_threads_batch(s->ctx)},{"audio_threads",ap.n_threads},
                          {"cache_type_k",ggml_type_name(cp.type_k)},{"cache_type_v",ggml_type_name(cp.type_v)},
                          {"cache_text_prefix",false},
                          {"flash_attention_setting",int(cp.flash_attn_type)},{"audio_sample_rate",mtmd_get_audio_sample_rate(s->audio)},
                          {"system_info",llama_print_system_info()},{"llama_version",llama_version()}};
        return s.release();
    } catch (const std::exception& e) { error=e.what(); return nullptr; }
}
__attribute__((visibility("default"))) void* edge_gguf_create(const char* model,const char* mmproj,int threads,int context,int max_new) {
    return edge_gguf_create_threads(model,mmproj,threads,threads,context,max_new);
}
__attribute__((visibility("default"))) int edge_gguf_set_prefix_cache(void* handle,int enabled) {
    auto* s=static_cast<State*>(handle);
    if (!s || (enabled!=0 && enabled!=1)) { error="invalid prefix cache setting"; return -1; }
    s->prefix_cache.invalidate();
    s->audio_windows.reset(); s->audio_window_kv_valid=false;
    s->prefix_cache.enabled=enabled!=0;
    llama_memory_clear(llama_get_memory(s->ctx),true);
    s->configuration["cache_text_prefix"]=s->prefix_cache.enabled;
    return 0;
}
// window=0은 확정/일반 추론. 별도 선택 설정에서 부분 요청 직전에만 켠다.
__attribute__((visibility("default"))) int edge_gguf_set_audio_window_ms(void* handle,int window,int reuse,int reset) {
    auto* s=static_cast<State*>(handle);
    if(!s || (window!=0 && window!=990 && window!=1000 && window!=1990 && window!=2000 && window!=4000 && window!=8000) ||
       (reuse!=0 && reuse!=1) || (reset!=0 && reset!=1)) return -1;
    if(reset || s->audio_window_ms!=window || s->audio_window_reuse!=bool(reuse) || !window) {
        s->audio_windows.reset(); s->audio_window_kv_valid=false;
    }
    s->audio_window_ms=window; s->audio_window_reuse=reuse;
    return 0;
}
__attribute__((visibility("default"))) int edge_gguf_set_audio_window(void* handle,int window,int reuse,int reset) {
    if(window!=0 && window!=1 && window!=2 && window!=4 && window!=8) return -1;
    return edge_gguf_set_audio_window_ms(handle,window*1000,reuse,reset);
}
// 한 호출에만 공급하는 진단 ABI. 토큰 후보는 출력/확정으로 취급하지 않는다.
__attribute__((visibility("default"))) int edge_gguf_set_token_draft(void* handle,const int32_t* tokens,size_t count,int block) {
    auto* s=static_cast<State*>(handle);
    if(!s) return -1;
    s->pending_token_draft.clear(); s->token_draft_block=1;
    if((block!=1 && block!=2 && block!=4 && block!=8) || count>size_t(s->max_new) || (count && !tokens)) return -1;
    const auto* vocab=llama_model_get_vocab(s->model);
    for(size_t i=0;i<count;++i)
        if(tokens[i]<0 || tokens[i]>=llama_vocab_n_tokens(vocab) || llama_vocab_is_eog(vocab,tokens[i])) return -1;
    if(count) s->pending_token_draft.assign(tokens,tokens+count);
    s->token_draft_block=block;
    return 0;
}
#ifdef EDGE_ASR_TRACE_TOKENS
__attribute__((visibility("default"))) int edge_gguf_set_token_trace(void* handle,int enabled) {
    auto* s=static_cast<State*>(handle);
    if(!s) return -1;
    s->pending_token_trace=false;
    if(enabled!=0 && enabled!=1) return -1;
    s->pending_token_trace=enabled!=0;return 0;
}
#endif
#ifdef EDGE_ASR_PROFILE_OPERATORS
__attribute__((visibility("default"))) int edge_gguf_set_operator_profile(void* handle,int enabled) {
    auto* s=static_cast<State*>(handle);
    if (!s || (enabled!=0 && enabled!=1)) { error="invalid operator profile setting"; return -1; }
    s->operators.enabled=enabled!=0; s->operators.reset(); return 0;
}
#endif
// 입력은 같은 batch PCM이다. callback은 토큰 바이트 전달만 하며 0 외 반환은 호출 실패로 남긴다.
typedef int (*edge_token_callback)(void*,const void*,size_t,int,double);
__attribute__((visibility("default"))) const char* edge_gguf_transcribe_tokens(void* handle,const float* samples,size_t count,edge_token_callback callback,void* user) {
    auto* s=static_cast<State*>(handle); if (!s) return nullptr;
#ifdef EDGE_ASR_TRACE_TOKENS
    TokenTrace trace(s->pending_token_trace,size_t(s->max_new));
    s->pending_token_trace=false;
    int trace_batch_rows=0;
#endif
#ifdef EDGE_ASR_PROFILE_OPERATORS
    s->operators.reset();
#endif
    json out={{"schema_version",1},{"status","failed"},{"input_samples",count},{"token_ids",json::array()},
              {"text_utf8_hex",""},{"termination","error"},{"first_generated_token_s",nullptr},{"first_visible_text_s",nullptr}};
    out["runtime_configuration"]=s->configuration;
    out["prefix_cache"]={{"enabled",s->prefix_cache.enabled},{"hit",false},{"prefix_tokens",0},
        {"reused_tokens",0},{"miss_reason","not_prepared"},{"valid_after_call",false}};
    out["audio_window"]={{"active",s->audio_window_ms>0},{"window_s",s->audio_window_ms/1000.0},
        {"window_ms",s->audio_window_ms},
        {"reuse",s->audio_window_reuse},{"kv_reused_tokens",0},
        {"policy","independent_windows_partial_only_v1"}};
    for (const char* key : {"prefix_eval_s","suffix_eval_s","audio_encode_s","audio_embedding_eval_s"}) out[key]=0.0;
    std::vector<int32_t> prefix;
    TokenDraft draft(std::move(s->pending_token_draft),s->token_draft_block);
    s->pending_token_draft.clear(); s->token_draft_block=1;
    std::vector<llama_token> ids; std::string text;
    double callback_s=0;
    const auto began=Clock::now();
    try {
        if (!samples || count<320) throw std::runtime_error("at least 2 feature frames required");
        auto stage=Clock::now();
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
        auto positions=mtmd_helper_get_n_pos(chunks.get());
        out["prompt_positions"]=positions; out["prompt_tokens"]=mtmd_helper_get_n_tokens(chunks.get());
        size_t audio_tokens=0;
        for(size_t i=0;i<mtmd_input_chunks_size(chunks.get());++i) {
            const auto* c=mtmd_input_chunks_get(chunks.get(),i);
            if(mtmd_input_chunk_get_type(c)==MTMD_INPUT_CHUNK_TYPE_AUDIO) audio_tokens+=mtmd_input_chunk_get_n_tokens(c);
        }
        out["audio_tokens"]=audio_tokens; out["preprocess_s"]=since(stage);
        std::vector<float> window_embeds;
        size_t reused_audio=0;
        if(s->audio_window_ms) {
            stage=Clock::now(); operator_phase(s,"audio_encoder");
            window_embeds=s->audio_windows.run(samples,count,size_t(s->audio_window_ms)*16,
                llama_model_n_embd_inp(s->model),s->audio_window_reuse,
                [&](const float* p,size_t n,bool full){return encode_audio_window(s,p,n,full);});
            out["audio_encode_s"]=since(stage);
            const size_t rows=window_embeds.size()/llama_model_n_embd_inp(s->model);
            positions=positions-llama_pos(audio_tokens)+llama_pos(rows);
            out["prompt_positions"]=positions; out["prompt_tokens"]=positions; out["audio_tokens"]=rows;
            out["audio_window"]["reused_samples"]=s->audio_windows.reused_samples;
            out["audio_window"]["computed_samples"]=s->audio_windows.computed_samples;
            out["audio_window"]["cache_bytes"]=s->audio_windows.bytes();
            if(s->audio_window_kv_valid) reused_audio=s->audio_windows.reused_tokens;
        }
        if (positions+s->max_new+1>int(llama_n_ctx(s->ctx))) { out["termination"]="context_limit"; throw std::runtime_error("full input plus output reservation exceeds context; no truncation"); }
        const auto* first=mtmd_input_chunks_get(chunks.get(),0);
        if (first && mtmd_input_chunk_get_type(first)==MTMD_INPUT_CHUNK_TYPE_TEXT && audio_tokens>0) {
            size_t n=0;
            const auto* tokens=mtmd_input_chunk_get_tokens_text(first,&n);
            if (n>0 && mtmd_input_chunk_get_n_pos(first)==llama_pos(n)) prefix.assign(tokens,tokens+n);
        }
        if (s->prefix_cache.enabled && prefix.empty()) throw std::runtime_error("no eligible text prefix before audio");
        out["prefix_cache"]["prefix_tokens"]=prefix.size();
        out["prefix_cache"]["token_ids"]=prefix;
        stage=Clock::now();
        llama_sampler_reset(s->sampler); llama_perf_context_reset(s->ctx);
        const char* miss=s->prefix_cache.begin(prefix,
            [&]{ llama_memory_clear(llama_get_memory(s->ctx),true); },
            [&](size_t n){ return llama_memory_seq_rm(llama_get_memory(s->ctx),0,llama_pos(n+reused_audio),-1); });
        const bool hit=miss==nullptr;
        if(!hit) reused_audio=0;
        out["audio_window"]["kv_reused_tokens"]=reused_audio;
        out["kv_reset_s"]=since(stage);
        out["prefix_cache"]["hit"]=hit;
        out["prefix_cache"]["reused_tokens"]=hit ? prefix.size() : 0;
        out["prefix_cache"]["miss_reason"]=miss ? json(miss) : json(nullptr);
        stage=Clock::now();
        llama_pos past=eval_prompt(s,chunks.get(),hit,prefix.size(),out,window_embeds,reused_audio);
        out["audio_and_prefill_s"]=since(stage)+(s->audio_window_ms?out["audio_encode_s"].get<double>():0);
        if (past!=positions) throw std::runtime_error("prompt position mismatch after prefix reuse");
        double decode_s=0,sample_s=0;int decode_calls=0,decode_rows=0,logit=-1;
        const auto* vocab=llama_model_get_vocab(s->model);
        for(int i=0;i<s->max_new;++i) {
            stage=Clock::now();llama_token token=llama_sampler_sample(s->sampler,s->ctx,logit);llama_sampler_accept(s->sampler,token);sample_s+=since(stage);
            const auto observed=draft.observe(token,size_t(i));
#ifdef EDGE_ASR_TRACE_TOKENS
            if(trace.enabled) trace.record(i,past,logit,trace_batch_rows,decode_calls,token,
                llama_get_logits_ith(s->ctx,logit),llama_vocab_n_tokens(vocab),observed.decoded,observed.trim);
#endif
            if(observed.trim && !llama_memory_seq_rm(llama_get_memory(s->ctx),0,past,-1))
                throw std::runtime_error("draft KV rollback failed");
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
            if(observed.decoded) { ++past;logit=observed.next_logit;continue; }
            stage=Clock::now();llama_batch_ext_clear(s->batch);
            const auto planned=draft.plan(token,size_t(i),size_t(std::min(s->max_new-i,int(llama_n_ctx(s->ctx))-past)));
            for(size_t row=0;row<planned.size();++row) {
                int idx=llama_batch_ext_add_token(s->batch,0,planned[row]);
                llama_pos pos=past+llama_pos(row);
                if(idx<0 || !llama_batch_ext_set_pos(s->batch,idx,&pos) || !llama_batch_ext_set_output_logits(s->batch,idx,true))
                    throw std::runtime_error("token batch preparation failed");
            }
            operator_phase(s,"token_decode");
            if(llama_process(s->ctx,LLAMA_PROCESS_TYPE_DECODE,s->batch)!=0) throw std::runtime_error("token decode failed");
#ifdef EDGE_ASR_TRACE_TOKENS
            trace_batch_rows=int(planned.size());
#endif
            ++past;++decode_calls;decode_rows+=int(planned.size());logit=0;decode_s+=since(stage);
        }
        out["token_decode_s"]=decode_s;out["token_decode_calls"]=decode_calls;out["sampling_s"]=sample_s;
        out["token_decode_evaluated_tokens"]=decode_rows;
        if(out["termination"]=="eog") out["status"]="success";
        else out["error"]="generation did not terminate with eog";
    } catch (const std::exception& e) { out["error"]=e.what(); }
    out["token_draft"]={{"policy","q8_greedy_verify_prefix_v1"},{"candidate_tokens",draft.size()},
        {"block",draft.block()},{"batches",draft.batches},{"proposed",draft.proposed},
        {"accepted",draft.accepted},{"rejected",draft.rejected}};
    s->prefix_cache.complete(prefix,out["status"]=="success");
    s->audio_window_kv_valid=s->audio_window_ms>0 && s->audio_window_reuse && out["status"]=="success";
    if(out["status"]!="success") s->audio_windows.reset();
    out["prefix_cache"]["valid_after_call"]=s->prefix_cache.valid();
    out["token_ids"]=ids;out["text_utf8_hex"]=hex(text);out["native_elapsed_s"]=since(began);
    out["token_callback_enabled"]=callback!=nullptr;out["token_callback_s"]=callback_s;
#ifdef EDGE_ASR_TRACE_TOKENS
    json decisions=json::array();
    const auto* trace_vocab=llama_model_get_vocab(s->model);
    for(const auto& row:trace.rows) decisions.push_back({{"index",row.index},{"position",row.position},
        {"logit_row",row.logit_row},{"batch_rows",row.batch_rows},{"decode_calls",row.decode_calls},
        {"token",row.token},{"token_piece_hex",row.valid?json(hex(piece(trace_vocab,row.token))):json(nullptr)},
        {"score",row.valid?json(row.score):json(nullptr)},
        {"runner",row.runner},{"runner_score",row.valid?json(row.runner_score):json(nullptr)},
        {"runner_piece_hex",row.valid?json(hex(piece(trace_vocab,row.runner))):json(nullptr)},
        {"margin",row.valid?json(double(row.score)-row.runner_score):json(nullptr)},
        {"decoded_candidate",row.decoded},{"trimmed_candidate_tail",row.trim},{"valid",row.valid}});
    out["token_trace"]={{"schema_version",1},{"enabled",trace.enabled},{"valid",trace.valid},
        {"scope","read-only selected and strongest competing raw logits; includes EOG; diagnostic timing only"},
        {"rows",decisions}};
#endif
#ifdef EDGE_ASR_PROFILE_OPERATORS
    out["operator_profile"]=profile_result(s->operators);
#endif
    s->result=out.dump();return s->result.c_str();
}
__attribute__((visibility("default"))) const char* edge_gguf_transcribe(void* handle,const float* samples,size_t count) {
    return edge_gguf_transcribe_tokens(handle,samples,count,nullptr,nullptr);
}
__attribute__((visibility("default"))) void edge_gguf_destroy(void* handle) { delete static_cast<State*>(handle); }
}
