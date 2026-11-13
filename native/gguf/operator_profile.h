// 연산별 진단 빌드용 집계. 실제 추론 시계만 받고 tensor payload는 읽지 않는다.
#pragma once
#include <array>
#include <cstdint>
#include <map>
#include <string>
#include <tuple>

struct OperatorNode {
    std::string name, op, lhs_name, lhs_type, rhs_type;
    std::array<int64_t, 4> shape{}, lhs_shape{}, rhs_shape{};
    auto key() const { return std::make_tuple(name, op, lhs_name, lhs_type, rhs_type); }
};

class OperatorProfile {
public:
    struct Sample {
        OperatorNode node;
        uint64_t count = 0, total_ns = 0, max_ns = 0;
        bool shape_changed = false;
    };
    using Key = std::pair<std::string, decltype(OperatorNode().key())>;
    bool enabled = false, valid = true;
    std::string phase = "unassigned";
    std::map<Key, Sample> groups;
    size_t overflow = 0;

    void reset() { groups.clear(); overflow = 0; valid = true; pending = nullptr; }
    void before(const void* node, uint64_t now) {
        if (pending || !node) valid = false;
        pending = node; began = now;
    }
    void after(const void* node, uint64_t now, const OperatorNode& info) {
        if (pending != node || !pending || now < began) { valid = false; pending = nullptr; return; }
        pending = nullptr;
        const auto key = std::make_pair(phase, info.key());
        auto it = groups.find(key);
        if (it == groups.end()) {
            if (groups.size() >= 8192) { ++overflow; return; }
            it = groups.emplace(key, Sample{info}).first;
        }
        auto& sample = it->second;
        sample.shape_changed |= sample.node.shape != info.shape || sample.node.lhs_shape != info.lhs_shape
            || sample.node.rhs_shape != info.rhs_shape;
        const auto elapsed = now - began;
        ++sample.count; sample.total_ns += elapsed;
        if (elapsed > sample.max_ns) sample.max_ns = elapsed;
    }
    bool complete() const { return valid && !pending && !overflow; }
private:
    const void* pending = nullptr;
    uint64_t began = 0;
};
