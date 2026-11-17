"""경계 재계산·전체 정규화·설정/오류 무효화·메모리 상한을 모델 없이 검사한다."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from edge_asr.config import load_evaluation_config


class FeatureCacheTests(unittest.TestCase):
    def test_cache_requires_manifest_and_explicit_supported_mode(self):
        root = Path(__file__).resolve().parents[2]
        base = (root / "configs/gguf.toml").read_text()
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary) / "config.toml"
            for mode in ("off", "on", "verify", "approximate"):
                for manifest in (False, True):
                    extra = f'audio_feature_cache_mode = "{mode}"\n'
                    if manifest:
                        extra += 'audio_feature_cache_manifest = "new/build.json"\n'
                    p.write_text(base.replace("[audio]", extra + "\n[audio]"))
                    with self.subTest(mode=mode, manifest=manifest):
                        if manifest and mode != "approximate":
                            self.assertEqual(
                                load_evaluation_config(p)["model"]["audio_feature_cache_mode"], mode
                            )
                        else:
                            with self.assertRaises(ValueError):
                                load_evaluation_config(p)

    @unittest.skipUnless(shutil.which("g++"), "C++ 캐시 경계 검사에 g++ 필요")
    def test_raw_prefix_cache_boundaries_normalization_equivalence_and_invalidation(self):
        root = Path(__file__).resolve().parents[2]
        code = r"""
#include "mel_prefix_cache.h"
#include <cassert>
#include <cmath>
#include <stdexcept>
std::vector<float> signal(int n,float tail=1) {
    std::vector<float> x(n+400);
    for(int i=0;i<n;++i)x[i+200]=float((i*17)%127)/128*(i>6400?tail:1);
    for(int i=0;i<200;++i){x[i]=x[400-i];x[n+200+i]=x[n+198-i];}
    return x;
}
bool original(const float* x,int n,float* out) {
    for(int m=0;m<128;++m)for(int f=0;f<n;++f){
        float sum=0;for(int j=0;j<400;++j)sum+=x[f*160+j]*(j%13+1);
        out[m*n+f]=std::log10(std::max(1e-10f,sum*sum))+m*.001f;
    }return true;
}
void normalize(std::vector<float>& x){
    double maximum=*std::max_element(x.begin(),x.end())-8;
    for(float& v:x)v=(std::max(double(v),maximum)+4)/4;
}
int main(){
    MelPrefixCache cache;std::vector<float> key{1,2};
    for(int n:{6400,12800,32000,80000}){
        auto x=signal(n,10000);int frames=n/160+1;
        std::vector<float> out(frames*128),ref(out.size());
        assert(cache.run(x.data(),frames,key,out.data(),2,original));
        assert(cache.equivalent);
        if(n>6400){assert(cache.reused>0);assert(cache.computed>0);}
        original(x.data(),frames,ref.data());normalize(out);normalize(ref);
        assert(!std::memcmp(out.data(),ref.data(),out.size()*4));
        assert(cache.bytes()<=1024*1024);
    }
    auto x=signal(6400);std::vector<float> out(128*41);
    assert(cache.run(x.data(),41,key,out.data(),1,original));assert(cache.reused==0);
    key[0]=3;assert(cache.run(x.data(),41,key,out.data(),1,original));assert(cache.reused==0);
    x[0]+=1;assert(cache.run(x.data(),41,key,out.data(),1,original));assert(cache.reused==0);
    cache.reset();assert(cache.frames==0);
    assert(cache.run(x.data(),41,key,out.data(),1,original));
    auto y=signal(12800);std::vector<float> larger(128*81),ref(larger.size());
    auto changed=[](const float* p,int n,float* out){original(p,n,out);for(int i=0;i<n*128;++i)out[i]+=n*.01f;return true;};
    x=signal(6400);cache.reset();cache.run(x.data(),41,key,out.data(),2,changed);
    assert(cache.run(y.data(),81,key,larger.data(),2,changed));
    assert(!cache.equivalent && cache.frames==0);
    changed(y.data(),81,ref.data());assert(!std::memcmp(larger.data(),ref.data(),larger.size()*4));
    auto fail=[](const float*,int,float*){return false;};
    assert(!cache.run(x.data(),41,key,out.data(),1,fail));assert(cache.frames==0);
    auto huge=signal(96000);std::vector<float> bigout(601*128);
    assert(cache.run(huge.data(),601,key,bigout.data(),1,original));assert(cache.frames==0);
    cache.release();assert(cache.bytes()==0);
    // 이전 capacity의 두 배보다 조금 작은 성장도 영구 메모리 상한을 넘지 않는다.
    for(int n:{24000,49000,79000,80000}){
        auto pcm=signal(n);int frames=n/160+1;std::vector<float> out(frames*128);
        assert(cache.run(pcm.data(),frames,key,out.data(),1,original));
        assert(cache.bytes()<=690112);
    }
    cache.release();assert(cache.bytes()==0);
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / "test.cpp").write_text(code)
            subprocess.run(
                [
                    shutil.which("g++"),
                    "-std=c++17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(root / "native/gguf"),
                    str(p / "test.cpp"),
                    "-o",
                    str(p / "test"),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(p / "test")], check=True, capture_output=True, timeout=20)


if __name__ == "__main__":
    unittest.main()
