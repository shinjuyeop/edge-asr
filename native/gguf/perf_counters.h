// Linux의 현재 작업자 스레드만 계측한다. 권한/용량/읽기 실패를 0으로 숨기지 않는다.
#pragma once
#include <array>
#include <cerrno>
#include <cstdint>
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace edge_perf {
constexpr int count = 6;
inline constexpr uint64_t events[2][count] = {
    {0x11, 0x08, 0x04, 0x03, 0x16, 0x17},
    {0x11, 0x08, 0x23, 0x24, 0x19, 0x05},
};
inline constexpr const char* names[2][count] = {
    {"cycles", "instructions", "l1d_access", "l1d_refill", "l2d_access", "l2d_refill"},
    {"cycles", "instructions", "stall_frontend", "stall_backend", "bus_access", "l1d_tlb_refill"},
};
struct Sample {
    uint64_t nr=0, enabled=0, running=0;
    std::array<uint64_t, count> values{};
};
inline bool difference(const Sample& before, const Sample& after, Sample& delta) {
    if (before.nr!=count || after.nr!=count || after.enabled<=before.enabled ||
        after.running<=before.running) return false;
    delta.nr=count; delta.enabled=after.enabled-before.enabled; delta.running=after.running-before.running;
    if (delta.running>delta.enabled) return false;
    for (int i=0;i<count;++i) {
        if (after.values[i]<before.values[i]) return false;
        delta.values[i]=after.values[i]-before.values[i];
    }
    return true;
}
class Group {
    std::array<int, count> fds{-1,-1,-1,-1,-1,-1};
    int bank_=0, type_=-1, error_=0;
public:
    Group() = default;
    Group(const Group&) = delete;
    Group& operator=(const Group&) = delete;
    ~Group() { close(); }
    void close() {
        // leader를 마지막에 닫아 부분 그룹이 독립적으로 살아남지 않게 한다.
        for (int i=count-1;i>=0;--i) if (fds[i]>=0) { ::close(fds[i]); fds[i]=-1; }
    }
    void configure(int bank, int type) {
        if (bank==bank_ && type==type_) return;
        close(); bank_=bank; type_=type; error_=0;
        if (!bank) return;
        if (bank<1 || bank>2 || type<0) { error_=EINVAL; return; }
        for (int i=0;i<count;++i) {
            perf_event_attr attr{};
            attr.size=sizeof(attr); attr.type=uint32_t(type); attr.config=events[bank-1][i];
            attr.disabled=i==0; attr.pinned=i==0;
            attr.exclude_kernel=1; attr.exclude_hv=1;
            attr.read_format=PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
            fds[i]=int(syscall(SYS_perf_event_open,&attr,0,-1,i==0 ? -1 : fds[0],PERF_FLAG_FD_CLOEXEC));
            if (fds[i]<0) { error_=errno; close(); return; }
        }
        if (ioctl(fds[0],PERF_EVENT_IOC_ENABLE,PERF_IOC_FLAG_GROUP)<0) { error_=errno; close(); }
    }
    int error() const { return error_; }
    bool read(Sample& sample) {
        if (fds[0]<0) return false;
        static_assert(sizeof(Sample)==sizeof(uint64_t)*(count+3), "group read ABI");
        const auto size=::read(fds[0],&sample,sizeof(sample));
        if (size!=ssize_t(sizeof(sample)) || sample.nr!=count) {
            error_=size<0 ? errno : EIO; return false;
        }
        return true;
    }
};
}
