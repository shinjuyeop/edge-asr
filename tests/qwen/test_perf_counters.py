"""Host PMU 권한 없이 그룹 수명·읽기 실패·누적 차분 계약을 검사한다."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("g++"), "Linux C++ 컴파일러가 필요합니다.")
class PerfCounterTests(unittest.TestCase):
    def test_group_cleanup_and_invalid_samples(self):
        fixture = r"""
#include <array>
#include <cerrno>
#include <cstdint>
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <cassert>
#include <cstdarg>
#include <cstring>
int opens=0, closes=0, fail_at=2, read_mode=0;
long mock_open(long number,...) {
    assert(number==SYS_perf_event_open);
    va_list args; va_start(args,number);
    auto* attr=va_arg(args,perf_event_attr*);
    assert(attr->exclude_kernel && attr->exclude_hv && !attr->inherit);
    assert(va_arg(args,int)==0 && va_arg(args,int)==-1);
    int leader=va_arg(args,int); va_end(args);
    if (leader==-1) assert(attr->pinned && attr->disabled);
    if (++opens==fail_at) { errno=EACCES; return -1; }
    return 10+opens;
}
int mock_ioctl(int,unsigned long,...) { return 0; }
int mock_close(int) { ++closes; return 0; }
ssize_t mock_read(int,void* buffer,size_t size) {
    uint64_t values[]={6,100,100,1,2,3,4,5,6};
    if (read_mode==1) return 0; // pinned group가 스케줄되지 못한 EOF도 실패
    std::memcpy(buffer,values,size); return ssize_t(size);
}
#define syscall mock_open
#define ioctl mock_ioctl
#define close mock_close
#define read mock_read
#include "perf_counters.h"
#undef syscall
#undef ioctl
#undef close
#undef read
int main() {
    using namespace edge_perf;
    Sample a{},b{},delta{};
    a.nr=b.nr=count; a.enabled=a.running=10; b.enabled=b.running=30;
    a.values.fill(3); b.values.fill(9);
    assert(difference(a,b,delta) && delta.enabled==20 && delta.running==20 && delta.values[0]==6);
    b.running=31; assert(!difference(a,b,delta)); b.running=20;
    assert(difference(a,b,delta) && delta.running==10); // 불완전 coverage는 호출자가 판정
    b.values[2]=1; assert(!difference(a,b,delta)); b.values[2]=9;
    b.nr=5; assert(!difference(a,b,delta)); b.nr=6;
    b.enabled=10; assert(!difference(a,b,delta));
    {
        Group g; g.configure(1,9);
        assert(g.error()==EACCES && closes==1 && !g.mock_read(a));
        g.configure(0,9); assert(g.error()==0);
        fail_at=-1; g.configure(2,9);
        assert(g.mock_read(a) && a.values[5]==6);
        read_mode=1; assert(!g.mock_read(a) && g.error()==EIO);
        g.configure(0,9); assert(closes==7);
        g.configure(2,9);
    }
    assert(closes==13);
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            cpp = Path(temporary) / "fixture.cpp"
            binary = cpp.with_suffix("")
            cpp.write_text(fixture)
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT / "native/gguf"),
                    str(cpp),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([str(binary)], check=True)
