// 음성 앞 text KV만 허용하는 상태 정책. 실제 KV 작업은 bridge가 제공한다.
#pragma once
#include <cstdint>
#include <vector>

class TextPrefixCache {
    std::vector<int32_t> tokens_;
    bool valid_ = false;
public:
    bool enabled = false;
    bool valid() const { return valid_; }
    void invalidate() { valid_ = false; tokens_.clear(); }

    template<class Clear, class Trim>
    const char* begin(const std::vector<int32_t>& prefix, Clear clear, Trim trim) {
        const char* reason = nullptr;
        if (!enabled) reason = "disabled";
        else if (prefix.empty()) reason = "empty_prefix";
        else if (!valid_) reason = "cold";
        else if (tokens_ != prefix) reason = "prefix_changed";
        else if (!trim(prefix.size())) reason = "trim_failed";
        if (reason) {
            invalidate();
            clear();
        }
        return reason; // nullptr이면 prefix를 남기고 그 이후 KV만 제거했다.
    }

    void complete(const std::vector<int32_t>& prefix, bool success) {
        if (enabled && success && !prefix.empty()) {
            tokens_ = prefix;
            valid_ = true;
        } else {
            invalidate();
        }
    }
};
