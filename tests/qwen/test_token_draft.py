"""greedy 후보 검증의 실제 C++ 상태 정책을 가짜 causal KV/logits로 검사한다."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("g++"), "C++ 후보 검증에 g++ 필요")
class TokenDraftTests(unittest.TestCase):
    def test_causal_rollback_rows_eog_and_output_limit(self):
        root = Path(__file__).resolve().parents[2]
        code = r"""
#include "token_draft.h"
#include <cassert>
#include <vector>
// 각 접두부에 대한 deterministic target. 틀린 KV가 남으면 이후 출력도 달라진다.
int next(const std::vector<int32_t>& prefix) {
    const std::vector<int32_t> target{3,5,8,13,21,34,55,89,144,233};
    for(size_t i=0;i<prefix.size();++i)
        if(i>=target.size() || prefix[i]!=target[i]) return 900+int(i);
    return prefix.size()==target.size() ? 0 : target[prefix.size()];
}
std::vector<int32_t> run(std::vector<int32_t> proposal,int block,int limit) {
    TokenDraft draft(proposal,block);
    std::vector<int32_t> kv, emitted, logits{next(kv)};
    size_t past=0; int row=0;
    for(int i=0;i<limit;++i) {
        int token=logits.at(row);
        auto state=draft.observe(token,size_t(i));
        if(state.trim) kv.resize(past);
        if(token==0) { assert(kv.size()==past);break; }
        emitted.push_back(token);
        if(i+1==limit) break;
        if(state.decoded) { ++past;row=state.next_logit;continue; }
        assert(kv.size()==past);
        auto batch=draft.plan(token,size_t(i),size_t(limit-i));
        assert(batch.size()<=size_t(block) && batch.size()<=size_t(limit-i));
        logits.clear();
        for(int32_t t:batch) { kv.push_back(t);logits.push_back(next(kv)); }
        ++past;row=0;
    }
    assert(draft.accepted+draft.rejected<=draft.proposed);
    return emitted;
}
int main() {
    const std::vector<int32_t> target{3,5,8,13,21,34,55,89,144,233};
    for(int block:{1,2,4,8}) for(int limit:{1,2,3,4,5,9,10,11,20}) {
        auto expected=target;expected.resize(std::min(expected.size(),size_t(limit)));
        for(size_t n=0;n<=target.size();++n) {
            std::vector<int32_t> proposal(target.begin(),target.begin()+n);
            assert(run(proposal,block,limit)==expected);
            // 모든 불일치 위치와 끝 초과 후보에서 잘못된 KV가 남지 않는다.
            for(size_t j=0;j<proposal.size();++j) {
                auto wrong=proposal;wrong[j]=999;
                assert(run(wrong,block,limit)==expected);
            }
            proposal.insert(proposal.end(),{999,998,997});
            assert(run(proposal,block,limit)==expected);
        }
    }
    TokenDraft cold({},4);assert(!cold.observe(3,0).decoded);
    assert(cold.plan(3,0,8)==std::vector<int32_t>{3});
    assert(cold.batches==0);
    TokenDraft d(target,4);d.observe(3,0);
    assert(d.plan(3,0,20)==std::vector<int32_t>({3,5,8,13}));
    assert(d.observe(5,1).next_logit==1);
    auto mismatch=d.observe(99,2);assert(mismatch.trim && !mismatch.decoded);
    assert(d.accepted==1 && d.rejected==2 && d.proposed==3);
    assert(d.plan(99,2,10)==std::vector<int32_t>{99});
}
"""
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / "draft.cpp"
            binary = Path(directory) / "draft"
            cpp.write_text(code)
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(root / "native/gguf"),
                    str(cpp),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
