"""선택 Q8 커널의 Host ELF 전달·모드·호출 집계 검사. ARM 수치는 RP5에서 검사한다."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("g++"), "ELF 모의 검사는 g++가 필요합니다.")
class Q8GEMVTests(unittest.TestCase):
    def test_opt_in_forwarding_and_unsupported_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            engine = folder / "engine.cpp"
            engine.write_text(r"""
#include <cstddef>
#include <dlfcn.h>
#include <cassert>
extern "C" void ggml_gemv_q8_0_4x4_q8_0(int n,float* out,size_t,const void*,const void*,int rows,int cols) {
    auto begin=reinterpret_cast<int(*)(int)>(dlsym(RTLD_DEFAULT,"edge_q8_gemv_begin"));
    assert(begin(0)==-1); // 전달된 커널 실행 중 모드 변경/집계 초기화 금지
    *out=float(n*rows*cols);
}
extern "C" float drive() {
    float out=0; ggml_gemv_q8_0_4x4_q8_0(32,&out,0,nullptr,nullptr,1,4); return out;
}
""")
            for source, name in (
                (engine, "engine.so"),
                (ROOT / "native/gguf/q8_gemv.cpp", "probe.so"),
            ):
                subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-O2",
                        "-shared",
                        "-fPIC",
                        "-U__ARM_FEATURE_DOTPROD",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        str(source),
                        "-ldl",
                        "-o",
                        str(folder / name),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            script = r"""
import ctypes,json,sys
p=ctypes.CDLL(None);e=ctypes.CDLL(sys.argv[1]);e.drive.restype=ctypes.c_float
p.edge_q8_gemv_calls.argtypes=[ctypes.c_int];p.edge_q8_gemv_calls.restype=ctypes.c_uint64
assert p.edge_q8_gemv_supported()==0
assert p.edge_q8_gemv_begin(1)==-1 and p.edge_q8_gemv_begin(2)==-1
assert p.edge_q8_gemv_begin(0)==0
assert e.drive()==128 and e.drive()==128
assert p.edge_q8_gemv_calls(0)==2 and p.edge_q8_gemv_calls(1)==0
assert p.edge_q8_gemv_begin(-1)==-1 and p.edge_q8_gemv_calls(0)==2
assert p.edge_q8_gemv_begin(0)==0 and p.edge_q8_gemv_calls(0)==0
assert e.drive()==128 and p.edge_q8_gemv_calls(0)==1
print(json.dumps({'passed':True}))
"""
            result = subprocess.run(
                [sys.executable, "-c", script, str(folder / "engine.so")],
                env={
                    **os.environ,
                    "LD_PRELOAD": str(folder / "probe.so"),
                    "EDGE_ASR_Q8_CPU": str(folder / "engine.so"),
                },
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(result.stdout)["passed"])


if __name__ == "__main__":
    unittest.main()
