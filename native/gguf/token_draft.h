// 후보 토큰은 Q8 greedy 판정과 일치할 때만 이미 계산한 KV/logits를 사용한다.
#pragma once
#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

class TokenDraft {
    std::vector<int32_t> proposal_, pending_;
    size_t consumed_ = 0;
    int block_;
    bool matching_ = true;
public:
    struct Observed { bool decoded = false, trim = false; int next_logit = -1; };
    size_t batches = 0, proposed = 0, accepted = 0, rejected = 0;
    TokenDraft(std::vector<int32_t> proposal, int block)
        : proposal_(std::move(proposal)), block_(block) {}
    size_t size() const { return proposal_.size(); }
    int block() const { return block_; }
    Observed observe(int32_t token, size_t position) {
        matching_ = matching_ && position < proposal_.size() && proposal_[position] == token;
        Observed out;
        if (consumed_ < pending_.size()) {
            if (pending_[consumed_] == token) {
                ++consumed_; ++accepted;
                out.decoded = true;
                out.next_logit = int(consumed_); // batch row0은 먼저 확인한 토큰이다.
            } else {
                rejected += pending_.size() - consumed_;
                out.trim = true;
                pending_.clear(); consumed_ = 0;
            }
        }
        return out;
    }
    std::vector<int32_t> plan(int32_t token, size_t position, size_t room) {
        pending_.clear(); consumed_ = 0;
        std::vector<int32_t> batch{token};
        if (matching_ && block_ > 1) {
            const auto count = std::min({size_t(block_), proposal_.size()-position, room});
            for (size_t i=1;i<count;++i) batch.push_back(proposal_[position+i]);
        }
        if (batch.size()>1) {
            pending_.assign(batch.begin()+1,batch.end());
            ++batches; proposed += pending_.size();
        }
        return batch;
    }
};
