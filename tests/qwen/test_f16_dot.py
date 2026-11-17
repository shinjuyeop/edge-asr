"""Host는 ELF 전달·집계만 검사한다. NEON 수치·속도는 RP5 별도 실측이다."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("g++"), "ELF 모의 검사에는 g++가 필요합니다.")
class F16DotTests(unittest.TestCase):
    def test_default_forwarding_counters_and_unsupported_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            engine = folder / "engine.cpp"
            engine.write_text(r"""
#include <cstddef>
#include <cstdint>
extern "C" void ggml_vec_dot_f16_f32(int n,float* out,size_t bs,const uint16_t*,size_t bx,const float*,size_t by,int nr) {
    *out=float(n+bs+bx+by+nr);
}
extern "C" float drive(int n) {
    float out=0; ggml_vec_dot_f16_f32(n,&out,2,nullptr,3,nullptr,4,1); return out;
}
""")
            for source, name in (
                (engine, "engine.so"),
                (ROOT / "native/gguf/f16_dot.cpp", "probe.so"),
            ):
                subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-O2",
                        "-shared",
                        "-fPIC",
                        "-U__ARM_NEON",
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
p.edge_f16_dot_calls.restype=ctypes.c_uint64
assert p.edge_f16_dot_supported()==0
for invalid in (-1,1,2):assert p.edge_f16_dot_begin(invalid)==-1
for bucket,n in enumerate([9,4320,7680,64]):
    assert e.drive(n)==n+10
    assert p.edge_f16_dot_calls(0,bucket)==1 and p.edge_f16_dot_calls(1,bucket)==0
assert p.edge_f16_dot_calls(-1,0)==0 and p.edge_f16_dot_calls(0,4)==0
assert p.edge_f16_dot_begin(0)==0
assert all(p.edge_f16_dot_calls(0,b)==0 for b in range(4))
assert e.drive(7680)==7690 and p.edge_f16_dot_calls(0,2)==1
assert p.edge_f16_dot_overflow()==0
print(json.dumps({'passed':True}))
"""
            result = subprocess.run(
                [sys.executable, "-c", script, str(folder / "engine.so")],
                env={
                    **os.environ,
                    "LD_PRELOAD": str(folder / "probe.so"),
                    "EDGE_ASR_F16_CPU": str(folder / "engine.so"),
                },
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(result.stdout)["passed"])


if __name__ == "__main__":
    unittest.main()
