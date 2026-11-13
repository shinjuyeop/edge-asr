"""모델 없는 ELF 호출 대조: 실제 함수 전달·단계/스레드·중첩 합계·끔 모드."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.environ.get("EDGE_ASR_LLAMA_SOURCE") and shutil.which("g++"),
    "커널 추적 ABI 검사는 고정 llama.cpp 헤더와 g++가 필요합니다.",
)
class KernelTraceTests(unittest.TestCase):
    def test_local_library_preemption_preserves_results_and_counts_worker_calls(self):
        source = Path(os.environ["EDGE_ASR_LLAMA_SOURCE"])
        includes = [
            item
            for path in (source / "include", source / "ggml/include", source / "tools/mtmd")
            for item in ("-I", str(path))
        ]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake = directory / "engine.cpp"
            fake.write_text(r"""
#include "llama.h"
#include "mtmd.h"
#include "mtmd-helper.h"
#include <thread>
#include <dlfcn.h>
#include <cassert>
extern "C" void quantize_row_q8_K(const float*,void*,int64_t) {}
extern "C" void quant(const float*,void*,int64_t,int64_t)
    __asm__("_Z19ggml_quantize_mat_tILl4EL9ggml_type15EEvPKfPvll");
extern "C" void quant(const float* a,void* b,int64_t,int64_t n) {
    quantize_row_q8_K(a,b,n); // 내부 row를 중복 합산하면 실패한다.
}
extern "C" void ggml_gemm_q4_K_8x4_q8_K(int n,float* out,size_t,const void*,const void*,int rows,int cols) {
    const auto reset=reinterpret_cast<int(*)(int)>(dlsym(RTLD_DEFAULT,"edge_kernel_trace_begin"));
    assert(reset(1)==-1); // 활성 단계/작업 중 초기화를 거부한다.
    *out=float(n*rows*cols);
}
extern "C" int32_t llama_process(llama_context*,llama_process_type,llama_batch_ext*) {
    float out[3]{};
    std::thread threads[3];
    for (int i=0;i<3;++i) threads[i]=std::thread([&,i] {
        quant(nullptr,nullptr,4,32);
        ggml_gemm_q4_K_8x4_q8_K(32,&out[i],1,nullptr,nullptr,4,8);
    });
    for (auto& thread:threads) thread.join();
    return int(out[0]+out[1]+out[2]);
}
extern "C" int32_t mtmd_helper_decode_image_chunk(mtmd_context*,llama_context* ctx,const mtmd_input_chunk*,
    float*,llama_pos,llama_seq_id,int32_t,llama_pos*,mtmd_helper_post_decode_callback,void*) {
    return llama_process(ctx,LLAMA_PROCESS_TYPE_DECODE,nullptr);
}
extern "C" int32_t fixture_drive() {
    return mtmd_helper_decode_image_chunk(nullptr,nullptr,nullptr,nullptr,0,0,512,nullptr,nullptr,nullptr);
}
""")
            trace = directory / "trace.so"
            engine = directory / "engine.so"
            for cpp, output in ((ROOT / "native/gguf/kernel_trace.cpp", trace), (fake, engine)):
                subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-O2",
                        "-shared",
                        "-fPIC",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        *includes,
                        str(cpp),
                        "-ldl",
                        "-pthread",
                        "-o",
                        str(output),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            child = r"""
import ctypes,json,sys
t=ctypes.CDLL(None)
t.edge_kernel_trace_begin.argtypes=[ctypes.c_int]
t.edge_kernel_trace_result.restype=ctypes.c_char_p
engine=ctypes.CDLL(sys.argv[1])
records=[]
assert t.edge_kernel_trace_begin(7)==-1
for on in (0,1,0,1):
    assert t.edge_kernel_trace_begin(on)==0
    value=engine.fixture_drive()
    record=json.loads(t.edge_kernel_trace_result())
    records.append(dict(value=value,trace=record))
print(json.dumps(records))
"""
            completed = subprocess.run(
                [sys.executable, "-c", child, str(engine)],
                env={
                    **os.environ,
                    "LD_PRELOAD": str(trace),
                    **{f"EDGE_ASR_TRACE_{name}": str(engine) for name in ("CPU", "MTMD", "LLAMA")},
                },
                capture_output=True,
                text=True,
                check=True,
            )
            for index, record in enumerate(json.loads(completed.stdout)):
                self.assertEqual(record["value"], 3072)
                trace_result = record["trace"]
                self.assertEqual(trace_result["enabled"], bool(index % 2))
                groups = trace_result["groups"]
                if index % 2 == 0:
                    self.assertEqual(groups, [])
                    continue
                self.assertEqual(len(groups), 2)
                self.assertEqual({row["stage"] for row in groups}, {"audio_prefill"})
                for row in groups:
                    self.assertEqual(row["calls"], 3)
                    self.assertGreater(row["worker_wall_ns"], 0)
                    self.assertGreater(row["worker_cpu_ns"], 0)
                    self.assertEqual(row["work"], 3072 if row["work_unit"] == "MAC" else 384)


if __name__ == "__main__":
    unittest.main()
