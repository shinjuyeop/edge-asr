// ORT 1.28.2 CPU C API의 호출 경계만 계측한다. 옵션·입출력·연산은 원본으로 전달한다.
#include "onnxruntime_c_api.h"
#include <dlfcn.h>
#include <time.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

namespace {
constexpr size_t kCapacity = 256;
struct Event { uint64_t kind, begin_ns, end_ns, failed; };
struct Trace { uint64_t abi, enabled, count, overflow; Event events[kCapacity]; };
struct Session { OrtSession* pointer = nullptr; uint64_t kind = 0; };
std::array<Session, 16> sessions;
std::mutex session_mutex;
const OrtApiBase* base = nullptr;
const OrtApi* original = nullptr;
OrtApi hooked;
OrtApiBase hooked_base;
std::once_flag initialized;
thread_local Trace trace{};
thread_local bool active = false;
uint64_t now() {
  timespec ts{};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return uint64_t(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}
uint64_t role(const char* path) {
  const char* name = strrchr(path, '/');
  name = name ? name + 1 : path;
  if (strcmp(name, "conv_frontend.onnx") == 0) return 1;
  if (strcmp(name, "encoder.int8.onnx") == 0) return 2;
  if (strcmp(name, "decoder.int8.onnx") == 0) return 3;
  return 0;
}
OrtStatus* ORT_API_CALL create(const OrtEnv* env, const char* path,
                             const OrtSessionOptions* options, OrtSession** out) noexcept {
  OrtStatus* status = original->CreateSession(env, path, options, out);
  if (!status) {
    std::lock_guard<std::mutex> lock(session_mutex);
    bool stored = false;
    for (auto& item : sessions) {
      if (!item.pointer) { item = {*out, role(path)}; stored = true; break; }
    }
    if (!stored) { fprintf(stderr, "edge-asr trace: session capacity exceeded\n"); abort(); }
  }
  return status;
}
void ORT_API_CALL release(OrtSession* session) noexcept {
  {
    std::lock_guard<std::mutex> lock(session_mutex);
    for (auto& item : sessions) if (item.pointer == session) item = {};
  }
  original->ReleaseSession(session);
}
OrtStatus* ORT_API_CALL run(OrtSession* session, const OrtRunOptions* options,
                           const char* const* names, const OrtValue* const* values, size_t input_count,
                           const char* const* outputs, size_t output_count, OrtValue** result) noexcept {
  if (!active) return original->Run(session, options, names, values, input_count, outputs, output_count, result);
  uint64_t kind = 0;
  // 세션 생성/해제는 순차 로딩·전사 경계에서만 허용한다. native Run은 같은 호출 스레드다.
  for (const auto& item : sessions) if (item.pointer == session) { kind = item.kind; break; }
  if (kind == 3) {
    bool seen = false;
    for (size_t i = 0; i < trace.count; ++i) if (trace.events[i].kind >= 3) { seen = true; break; }
    kind = seen ? 4 : 3;
  }
  const uint64_t start = now();
  OrtStatus* status = original->Run(session, options, names, values, input_count, outputs, output_count, result);
  const uint64_t end = now();
  if (trace.count < kCapacity) trace.events[trace.count++] = {kind, start, end, status ? 1ULL : 0ULL};
  else trace.overflow++;
  return status;
}
const OrtApi* ORT_API_CALL api(uint32_t version) noexcept {
  return original && base->GetApi(version) ? &hooked : nullptr;
}
const char* ORT_API_CALL version() noexcept { return base->GetVersionString(); }
void initialize() {
  const char* path = getenv("EDGE_ASR_ORT_LIBRARY");
  if (!path || !*path) { fprintf(stderr, "edge-asr trace: EDGE_ASR_ORT_LIBRARY required\n"); abort(); }
  void* handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
  auto entry = handle ? reinterpret_cast<const OrtApiBase*(*)()>(dlsym(handle, "OrtGetApiBase")) : nullptr;
  if (!entry) { fprintf(stderr, "edge-asr trace: original OrtGetApiBase not found\n"); abort(); }
  base = entry();
  if (strcmp(base->GetVersionString(), "1.28.2") != 0) {
    fprintf(stderr, "edge-asr trace: only ORT 1.28.2 ABI supported\n"); abort();
  }
  original = base->GetApi(ORT_API_VERSION);
  if (!original) { fprintf(stderr, "edge-asr trace: ORT API 28 unavailable\n"); abort(); }
  hooked = *original;
  hooked.CreateSession = create;
  hooked.ReleaseSession = release;
  hooked.Run = run;
  hooked_base = {api, version};
}
} // namespace
extern "C" {
ORT_EXPORT const OrtApiBase* ORT_API_CALL OrtGetApiBase() noexcept {
  std::call_once(initialized, initialize);
  return &hooked_base;
}
ORT_EXPORT uint64_t EdgeTraceInitialize() { OrtGetApiBase(); return 1; }
ORT_EXPORT uint64_t EdgeTraceSessionMask() {
  uint64_t mask = 0;
  std::lock_guard<std::mutex> lock(session_mutex);
  for (const auto& item : sessions) if (item.pointer && item.kind) mask |= (1ULL << item.kind);
  return mask;
}
ORT_EXPORT void EdgeTraceBegin(uint64_t enabled) {
  trace = {}; trace.abi = 1; trace.enabled = enabled; active = enabled != 0;
}
ORT_EXPORT const Trace* EdgeTraceEnd() { active = false; return &trace; }
}
