// 실제 모델이 없는 C API 전달/범위/overflow 검사. ARM ASR 추론 결과가 아니다.
#include "onnxruntime_c_api.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>

#ifdef EDGE_FAKE_ORT
namespace {
uint64_t calls = 0;
OrtStatus* ORT_API_CALL create(const OrtEnv*, const char*, const OrtSessionOptions*, OrtSession** out) noexcept {
  *out = reinterpret_cast<OrtSession*>(new uint64_t{42}); return nullptr;
}
OrtStatus* ORT_API_CALL run(OrtSession*, const OrtRunOptions*, const char* const* names,
                           const OrtValue* const*, size_t count, const char* const*, size_t outputs,
                           OrtValue** out) noexcept {
  assert(count == 1 && outputs == 1); calls++;
  if (strcmp(names[0], "fail") == 0) return reinterpret_cast<OrtStatus*>(99);
  out[0] = reinterpret_cast<OrtValue*>(42); return nullptr;
}
void ORT_API_CALL release(OrtSession* p) noexcept { delete reinterpret_cast<uint64_t*>(p); }
const OrtApi* ORT_API_CALL api(uint32_t version) noexcept {
  static OrtApi result{}; result.CreateSession = create; result.Run = run; result.ReleaseSession = release;
  return version <= 28 ? &result : nullptr;
}
const char* ORT_API_CALL version() noexcept { return "1.28.2"; }
}
extern "C" {
ORT_EXPORT const OrtApiBase* ORT_API_CALL OrtGetApiBase() noexcept {
  static OrtApiBase base{api, version}; return &base;
}
ORT_EXPORT uint64_t FakeCalls() { return calls; }
}
#else
struct Event { uint64_t kind, begin_ns, end_ns, failed; };
struct Trace { uint64_t abi, enabled, count, overflow; Event events[256]; };
int main() {
  auto entry = reinterpret_cast<const OrtApiBase*(*)()>(dlsym(RTLD_DEFAULT, "OrtGetApiBase"));
  auto begin = reinterpret_cast<void(*)(uint64_t)>(dlsym(RTLD_DEFAULT, "EdgeTraceBegin"));
  auto end = reinterpret_cast<const Trace*(*)()>(dlsym(RTLD_DEFAULT, "EdgeTraceEnd"));
  auto mask = reinterpret_cast<uint64_t(*)()>(dlsym(RTLD_DEFAULT, "EdgeTraceSessionMask"));
  assert(entry && begin && end && mask);
  const OrtApi* api = entry()->GetApi(28);
  void* fake = dlopen(getenv("EDGE_ASR_ORT_LIBRARY"), RTLD_NOW);
  auto calls = reinterpret_cast<uint64_t(*)()>(dlsym(fake, "FakeCalls"));
  assert(calls);
  OrtSession* sessions[3]{};
  const char* paths[] = {"/model/conv_frontend.onnx", "/model/encoder.int8.onnx", "/model/decoder.int8.onnx"};
  for (int i = 0; i < 3; ++i) assert(!api->CreateSession(nullptr, paths[i], nullptr, &sessions[i]));
  assert(mask() == 14);
  auto run = [&](int index, bool fail = false) {
    const char* names[] = {fail ? "fail" : "input"};
    const char* output_names[] = {"output"};
    const OrtValue* inputs[] = {nullptr}; OrtValue* outputs[] = {nullptr};
    auto status = api->Run(sessions[index], nullptr, names, inputs, 1, output_names, 1, outputs);
    if (fail) assert(status == reinterpret_cast<OrtStatus*>(99));
    else assert(!status && outputs[0] == reinterpret_cast<OrtValue*>(42));
  };
  for (int inference = 0; inference < 2; ++inference) {
    begin(1); run(0); run(1); run(2); run(2);
    const Trace* result = end();
    assert(result->abi == 1 && result->count == 4 && result->overflow == 0);
    for (int i = 0; i < 4; ++i) {
      assert(result->events[i].kind == uint64_t(i + 1));
      assert(result->events[i].end_ns >= result->events[i].begin_ns);
    }
  }
  begin(0); run(0); run(1); run(2); assert(end()->count == 0); assert(calls() == 11);
  begin(1); run(0); run(1); for (int i = 0; i < 260; ++i) run(2);
  assert(end()->count == 256 && end()->overflow == 6);
  begin(1); run(2, true); assert(end()->events[0].failed == 1);
  for (auto p : sessions) api->ReleaseSession(p);
  assert(mask() == 0);
  puts("native mock API forwarding, reset, disabled, overflow, error and release passed");
}
#endif
