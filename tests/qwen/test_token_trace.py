"""토큰 선택 진단의 읽기 전용·경계·잘못된 logits 기록을 실제 C++로 검사한다."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("g++"), "C++ 토큰 선택 진단 검사에 g++ 필요")
class TokenTraceTests(unittest.TestCase):
    def test_selection_ties_invalid_scores_bounds_and_read_only_input(self):
        root = Path(__file__).resolve().parents[2]
        source = r"""
#include "token_trace.h"
#include <cassert>
#include <cstring>
int main() {
    const float inf=std::numeric_limits<float>::infinity();
    float scores[]{-inf,2.0f,1.75f,1.25f};
    float saved[4];std::memcpy(saved,scores,sizeof(scores));
    TokenTrace trace(true,2);
    trace.record(0,42,-1,0,0,1,scores,4,false,false);
    auto a=trace.rows.at(0);
    assert(a.valid && a.token==1 && a.runner==2 && a.score==2.0f && a.runner_score==1.75f);
    assert(a.index==0 && a.position==42 && a.logit_row==-1 && a.batch_rows==0);
    assert(std::memcmp(saved,scores,sizeof(scores))==0);
    scores[3]=2.0f;trace.record(1,43,1,4,1,3,scores,4,true,false);
    auto b=trace.rows.at(1);
    assert(b.valid && b.runner==1 && b.score==b.runner_score && b.decoded);
    trace.record(2,44,2,4,1,1,scores,4,true,false);
    assert(!trace.valid && trace.rows.size()==2);
    TokenTrace off(false,2);off.record(0,0,0,0,0,-1,nullptr,0,false,false);
    assert(off.valid && off.rows.empty());
    for(int problem=0;problem<6;++problem) {
        TokenTrace bad(true,1);float xs[]{0,2,1};
        if(problem==0) xs[0]=std::numeric_limits<float>::quiet_NaN();
        if(problem==1) xs[0]=inf;
        if(problem==2) xs[1]=-inf;
        bad.record(0,0,0,1,1,problem==3?4:problem==4?0:1,
                   problem==5?nullptr:xs,3,false,true);
        assert(!bad.valid && bad.rows.size()==1 && !bad.rows[0].valid && bad.rows[0].trim);
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            cpp, binary = Path(directory) / "trace.cpp", Path(directory) / "trace"
            cpp.write_text(source)
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
