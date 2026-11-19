// 선택 진단 전용. logits는 읽기만 하고 출력 토큰/샘플러/KV는 변경하지 않는다.
#pragma once
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

struct TokenDecision {
    int index, position, logit_row, batch_rows, decode_calls, token, runner;
    float score, runner_score;
    bool decoded, trim, valid;
};

class TokenTrace {
    size_t limit_;
public:
    bool enabled, valid = true;
    std::vector<TokenDecision> rows;
    TokenTrace(bool enabled, size_t limit) : limit_(limit), enabled(enabled) {}
    void record(int index, int position, int row, int batch_rows, int calls,
                int token, const float* logits, int vocabulary, bool decoded, bool trim) {
        if(!enabled) return;
        if(rows.size()>=limit_) { valid=false;return; }
        TokenDecision out{index,position,row,batch_rows,calls,token,-1,0,0,decoded,trim,false};
        if(logits && token>=0 && token<vocabulary && vocabulary>=2) {
            out.score=logits[token];out.runner_score=-std::numeric_limits<float>::infinity();
            bool finite_or_negative_infinity=true;
            for(int i=0;i<vocabulary;++i) {
                if(std::isnan(logits[i]) || logits[i]==std::numeric_limits<float>::infinity())
                    finite_or_negative_infinity=false;
                if(i!=token && logits[i]>out.runner_score) {
                    out.runner=i;out.runner_score=logits[i];
                }
            }
            out.valid=finite_or_negative_infinity && std::isfinite(out.score) &&
                std::isfinite(out.runner_score) && out.score>=out.runner_score;
        }
        valid=valid && out.valid;rows.push_back(out);
    }
};
