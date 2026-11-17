// 선택 부분 전사용 독립 음성 창. 전체 구간 인코딩과 동등하다고 가정하지 않는다.
#pragma once
#include <cstring>
#include <stdexcept>
#include <vector>

class AudioWindowCache {
    std::vector<float> pcm_, encoded_;
    size_t window_ = 0, dim_ = 0;
public:
    size_t reused_samples = 0, computed_samples = 0, reused_tokens = 0;
    void reset() { pcm_.clear(); encoded_.clear(); }
    size_t bytes() const { return (pcm_.capacity()+encoded_.capacity())*sizeof(float); }

    template<class Encode>
    std::vector<float> run(const float* pcm, size_t count, size_t window, size_t dim,
                          bool reuse, Encode encode) {
        if (!pcm || count<320 || count>80000 || !dim || dim>4096 ||
            (window!=16000 && window!=32000 && window!=64000 && window!=128000))
            throw std::invalid_argument("partial audio window bounds");
        size_t full=count/window*window;
        // 20ms 미만 잔여 입력은 앞 창과 함께 계산한다. 음성을 버리거나 0으로 대체하지 않는다.
        if (count>full && count-full<320 && full>=window) full-=window;
        if (!reuse || window_!=window || dim_!=dim || pcm_.size()>full ||
            (!pcm_.empty() && std::memcmp(pcm_.data(),pcm,pcm_.size()*sizeof(float)))) reset();
        window_=window; dim_=dim;
        reused_samples=pcm_.size(); computed_samples=0; reused_tokens=encoded_.size()/dim;
        try {
            // 최대로 저장할 5초 입력 크기를 넘는 vector 성장도 막는다.
            if (pcm_.capacity()<80000) pcm_.reserve(80000);
            if (encoded_.capacity()<128*dim) encoded_.reserve(128*dim);
            for (size_t start=pcm_.size();start<full;start+=window) {
                auto result=encode(pcm+start,window,true);
                if (result.empty() || result.size()%dim || result.size()>128*dim-encoded_.size())
                    throw std::runtime_error("invalid window embedding shape");
                encoded_.insert(encoded_.end(),result.begin(),result.end());
                pcm_.insert(pcm_.end(),pcm+start,pcm+start+window);
                computed_samples+=window;
            }
            auto output=encoded_;
            if (full<count) {
                auto tail=encode(pcm+full,count-full,false);
                if (tail.empty() || tail.size()%dim || tail.size()>128*dim-output.size())
                    throw std::runtime_error("invalid tail embedding shape");
                output.insert(output.end(),tail.begin(),tail.end());
                computed_samples+=count-full;
            }
            return output;
        } catch (...) { reset(); throw; }
    }
};
