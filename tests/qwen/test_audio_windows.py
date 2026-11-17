"""완료 창 재사용의 범위·바이트 동등성과 부분/확정 격리를 검사한다."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from edge_asr.qwen.audio_windows import PartialAudioWindows, validate_audio_windows


class AudioWindowsTests(unittest.TestCase):
    def test_explicit_configuration_and_final_fallback(self):
        model = {"partial_audio_window_s": 2, "cache_text_prefix": True}
        for value in (0, -1, True, 0.5, 3, 16):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_audio_windows({**model, "partial_audio_window_s": value})
        with self.assertRaises(ValueError):
            PartialAudioWindows(model, {"features": []})
        with self.assertRaises(ValueError):
            validate_audio_windows({**model, "audio_feature_cache_manifest": "old.json"})
        library = Mock()
        library.edge_gguf_set_audio_window.return_value = 0
        windows = PartialAudioWindows(model, {"features": ["partial_audio_window_v1"]})
        windows.bind(library, 123)
        for kind, expected in (("partial", 2), ("partial", 2), ("final", 0)):
            windows.prepare(kind)
            windows.begin()
            library.edge_gguf_set_audio_window.assert_called_with(123, expected, 1, 0)
            windows.verify({"audio_window": {"active": bool(expected), "window_s": expected}})
        # 준비되지 않은 일반 batch 호출에는 부분 설정이 새어 나오지 않는다.
        windows.prepare("partial")
        windows.begin()
        windows.begin()
        library.edge_gguf_set_audio_window.assert_called_with(123, 0, 1, 0)
        windows.reset()
        library.edge_gguf_set_audio_window.assert_called_with(123, 0, 1, 1)
        with self.assertRaises(RuntimeError):
            windows.verify({"audio_window": {"active": True, "window_s": 2}})
        windows.close()

    def test_frame_aligned_windows_require_new_capability(self):
        for size in (990, 1990):
            model = {"partial_audio_window_ms": size, "cache_text_prefix": True}
            with self.assertRaises(ValueError):
                PartialAudioWindows(model, {"features": ["partial_audio_window_v1"]})
            windows = PartialAudioWindows(model, {"features": ["partial_audio_window_ms_v1"]})
            library = Mock()
            library.edge_gguf_set_audio_window_ms.return_value = 0
            windows.bind(library, 321)
            windows.prepare("partial")
            windows.begin()
            library.edge_gguf_set_audio_window_ms.assert_called_with(321, size, 1, 0)
            windows.verify({"audio_window": {"active": True, "window_ms": size}})
            windows.prepare("final")
            windows.begin()
            library.edge_gguf_set_audio_window_ms.assert_called_with(321, 0, 1, 0)
        with self.assertRaises(ValueError):
            validate_audio_windows({**model, "partial_audio_window_s": 2})

    @unittest.skipUnless(shutil.which("g++"), "C++ 창 캐시 검사에 g++ 필요")
    def test_cached_and_recomputed_windows_match_at_boundaries(self):
        root = Path(__file__).resolve().parents[2]
        code = r"""
#include "audio_window_cache.h"
#include <cassert>
#include <cmath>
int main() {
    AudioWindowCache cached, reference;
    std::vector<float> pcm(80000);
    for(size_t i=0;i<pcm.size();++i) pcm[i]=std::sin(float(i)*0.03f);
    auto encode=[](const float* p,size_t n,bool full) {
        const size_t rows=full?((n/160+99)/100)*13:(n/16000+1)*13;
        std::vector<float> out(rows*4);
        float sum=0;for(size_t i=0;i<n;++i)sum+=p[i];
        for(size_t i=0;i<out.size();++i)out[i]=sum+float(i);
        return out;
    };
    for(size_t w:{15840,16000,31840,32000,64000,128000}) {
        cached.reset();
        for(size_t n:{320,9600,16000,16100,16320,22400,32000,32100,32320,35200,48000,64000,80000}) {
            auto a=cached.run(pcm.data(),n,w,4,true,encode);
            auto b=reference.run(pcm.data(),n,w,4,false,encode);
            assert(a==b);
            assert(cached.reused_samples+cached.computed_samples==n);
            assert(cached.bytes()<=80000*4+128*4*4);
        }
    }
    cached.reset();
    cached.run(pcm.data(),35200,16000,4,true,encode);
    cached.run(pcm.data(),48000,16000,4,true,encode);
    assert(cached.reused_samples==32000 && cached.reused_tokens==26);
    pcm[100]+=1;
    auto a=cached.run(pcm.data(),64000,16000,4,true,encode);
    assert(cached.reused_samples==0);
    assert(a==reference.run(pcm.data(),64000,16000,4,false,encode));
    cached.run(pcm.data(),9600,16000,4,true,encode);
    assert(cached.reused_samples==0);
    try {
        cached.run(pcm.data(),48000,16000,4,true,
            [](const float*,size_t,bool)->std::vector<float>{throw std::runtime_error("failure");});
        assert(false);
    } catch(const std::runtime_error&) {}
    cached.run(pcm.data(),64000,16000,4,true,encode);
    assert(cached.reused_samples==0);
    cached.reset();
    cached.run(pcm.data(),64000,16000,4,true,encode);
    assert(cached.reused_samples==0);
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)
            (p / "test.cpp").write_text(code)
            subprocess.run(
                [
                    "g++",
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
