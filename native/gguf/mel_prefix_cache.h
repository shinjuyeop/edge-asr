// 정규화 전 log-Mel만 보관한다. 반사 패딩을 포함한 입력 창이 비트 동일한 프레임만 재사용한다.
#pragma once
#include <algorithm>
#include <cstring>
#include <vector>

struct MelPrefixCache {
    static constexpr int max_frames = 501, mel = 128, fft = 400, hop = 160;
    std::vector<float> pcm, features, parameters;
    int frames = 0, reused = 0, computed = 0;
    bool equivalent = true;
    void reset() { pcm.clear(); features.clear(); parameters.clear(); frames = 0; }
    size_t bytes() const { return 4 * (pcm.capacity() + features.capacity() + parameters.capacity()); }
    void release() { reset(); pcm.shrink_to_fit(); features.shrink_to_fit(); parameters.shrink_to_fit(); }

    template<class Compute>
    bool run(const float* input, int count, const std::vector<float>& key, float* out, int mode, Compute compute) {
        reused = 0; computed = count; equivalent = true;
        if (!mode || count < 1 || count > max_frames || key.size() > 28000) {
            reset(); return compute(input, count, out);
        }
        // 증분 assign의 기하급수 capacity 증가 대신 고정 상한을 한 번 예약한다.
        // PCM 321600 + Mel 256512 + 설정 112000 = 690112 bytes 이하.
        pcm.reserve(size_t(max_frames - 1) * hop + fft);
        features.reserve(size_t(max_frames) * mel);
        parameters.reserve(28000);
        const size_t length = size_t(count - 1) * hop + fft;
        if (parameters.size() != key.size() || std::memcmp(parameters.data(), key.data(), key.size()*4) || count < frames) reset();
        size_t common = 0;
        while (common < std::min(length, pcm.size()) && !std::memcmp(input+common, pcm.data()+common, 4)) ++common;
        if (common >= fft) reused = std::min({frames, count, int((common-fft)/hop+1)});
        computed = count - reused;
        std::vector<float> tail(size_t(computed)*mel);
        if (computed && !compute(input + size_t(reused)*hop, computed, tail.data())) { reset(); return false; }
        for (int m=0; m<mel; ++m) {
            if (reused) std::copy_n(features.data()+size_t(m)*frames, reused, out+size_t(m)*count);
            if (computed) std::copy_n(tail.data()+size_t(m)*computed, computed, out+size_t(m)*count+reused);
        }
        if (mode == 2) {
            std::vector<float> reference(size_t(count)*mel);
            if (!compute(input, count, reference.data())) { reset(); return false; }
            equivalent = !std::memcmp(reference.data(), out, reference.size()*4);
            if (!equivalent) {
                std::copy(reference.begin(), reference.end(), out);
                reset(); return true; // 검증 모드는 불일치 시 정확한 원래 출력만 반환한다.
            }
        }
        pcm.assign(input, input+length); features.assign(out, out+size_t(count)*mel);
        parameters = key; frames = count;
        return true;
    }
};
