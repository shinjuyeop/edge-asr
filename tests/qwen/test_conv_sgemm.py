"""Host ELF 모의 검사. 실제 FP16 수치/속도는 RP5에서 별도 검증한다."""

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
    "고정 llama.cpp 헤더와 g++가 필요합니다.",
)
class ConvSGEMMTests(unittest.TestCase):
    def test_shape_routing_workers_reset_and_patch_scope(self):
        source = Path(os.environ["EDGE_ASR_LLAMA_SOURCE"])
        includes = [
            item
            for p in (source / "ggml/include", source / "ggml/src", source / "ggml/src/ggml-cpu")
            for item in ("-I", str(p))
        ]
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            original = source / "ggml/src/ggml-cpu/llamafile/sgemm.cpp"
            before = original.read_bytes()
            subprocess.run(
                [
                    "patch",
                    "--fuzz=0",
                    "--output",
                    str(folder / "sgemm.cpp"),
                    str(original),
                    str(ROOT / "native/gguf/conv_sgemm.patch"),
                ],
                check=True,
                capture_output=True,
            )
            generated = (folder / "sgemm.cpp").read_text()
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual(generated.count("BLOCK_SIZE<EDGE_CONV_COLUMNS>"), 1)
            self.assertEqual(generated.count("mnpack<4, EDGE_CONV_COLUMNS, 4>"), 1)
            self.assertNotIn(
                "EDGE_CONV_COLUMNS", generated[generated.index("class tinyBLAS_RVV") :]
            )
            common = r"""
#include "ggml-cpu-impl.h"
#include "llamafile/sgemm.h"
#include <dlfcn.h>
#include <cassert>
#include <thread>
#define ARGS const ggml_compute_params* p,int64_t m,int64_t,int64_t,const void*,int64_t,const void*,int64_t,void* c,int64_t,int,int,int
bool check(const ggml_compute_params* p,int64_t m,void* c,int extra) {
    auto reset=reinterpret_cast<int(*)(int)>(dlsym(RTLD_DEFAULT,"edge_conv_begin"));
    auto result=reinterpret_cast<const char*(*)()>(dlsym(RTLD_DEFAULT,"edge_conv_result"));
    assert(reset(0)==-1 && result()==nullptr);
    *static_cast<float*>(c)=float(m+p->ith+extra);return true;
}
"""
            (folder / "fake.cpp").write_text(
                common
                + r"""
extern "C" bool llamafile_sgemm(ARGS) { return check(p,m,c,0); }
extern "C" float drive(int m,int type) {
    float out[3]{};std::thread threads[3];
    for(int i=0;i<3;++i) threads[i]=std::thread([&,i] {
        ggml_compute_params p{};p.ith=i;p.nth=3;
        assert(llamafile_sgemm(&p,m,480,4320,nullptr,4320,nullptr,4320,&out[i],m,type,GGML_TYPE_F16,GGML_TYPE_F32));
    });
    for(auto& t:threads)t.join();
    return out[0]+out[1]+out[2];
}
"""
            )
            (folder / "variants.cpp").write_text(
                common
                + r"""
extern "C" bool edge_conv6(ARGS) { return check(p,m,c,6); }
extern "C" bool edge_conv4(ARGS) { return check(p,m,c,4); }
"""
            )
            for files, output in (
                ([folder / "fake.cpp"], "engine.so"),
                ([ROOT / "native/gguf/conv_sgemm.cpp", folder / "variants.cpp"], "probe.so"),
            ):
                subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-O2",
                        "-fPIC",
                        "-shared",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-Wno-unused-function",
                        *includes,
                        *map(str, files),
                        "-ldl",
                        "-pthread",
                        "-o",
                        str(folder / output),
                    ],
                    check=True,
                    capture_output=True,
                )
            script = r"""
import ctypes,json,sys
e=ctypes.CDLL(sys.argv[1]);e.drive.restype=ctypes.c_float
p=ctypes.CDLL(None);p.edge_conv_result.restype=ctypes.c_char_p
for mode,extra in [(0,0),(1,6),(2,4),(0,0)]:
 assert p.edge_conv_begin(mode)==0
 for m in [800,1600,2400,3200,4000,208,416,624,832,1040]:assert e.drive(m,1)==3*m+3+3*extra
 assert e.drive(16,1)==51 and e.drive(800,0)==2403
 r=json.loads(p.edge_conv_result());assert r['mode']==mode and r['other_forwarded']==6
 assert len(r['groups'])==30
 for g in r['groups']:assert g['calls']==g['accepted']==1 and g['cpu_ns']>0 and g['wall_ns']>0
 for invalid in [-1,3]:assert p.edge_conv_begin(invalid)==-1
 assert json.loads(p.edge_conv_result())==r
assert p.edge_conv_begin(0)==0 and json.loads(p.edge_conv_result())['groups']==[]
print(json.dumps({'passed':True}))
"""
            result = subprocess.run(
                [sys.executable, "-c", script, str(folder / "engine.so")],
                env={
                    **os.environ,
                    "LD_PRELOAD": str(folder / "probe.so"),
                    "EDGE_ASR_CONV_CPU": str(folder / "engine.so"),
                },
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(result.stdout)["passed"])
