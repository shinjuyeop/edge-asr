#pragma once

#include <chrono>
#include <cstdint>
#include <cstring>
#include <future>
#include <mutex>
#include <vector>

// 한 확정 요청의 PCM 사본만 소유한다. 같은 mtmd_context는 직전 요청의
// CPU decode 중에만 넘기고, 다음 요청의 mtmd 호출 전에 반드시 회수한다.
class FinalAudioPreparation {
    using Clock = std::chrono::steady_clock;
public:
    struct Snapshot {
        int64_t job_id = -1;
        std::vector<float> pcm, embedding;
        double compute_s = 0, wait_s = 0;
        bool matched = false;
    };

    template<class Encoder>
    bool offer(int64_t job_id, const float* pcm, size_t count, Encoder encode) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!decoding || work.valid()) return false;
        Snapshot snapshot;
        snapshot.job_id = job_id;
        snapshot.pcm.assign(pcm, pcm + count);
        work = std::async(std::launch::async, [snapshot=std::move(snapshot), encode]() mutable {
            const auto start = Clock::now();
            snapshot.embedding = encode(snapshot.pcm.data(), snapshot.pcm.size());
            snapshot.compute_s = std::chrono::duration<double>(Clock::now()-start).count();
            return std::move(snapshot);
        });
        return true;
    }

    void decode_phase(bool active) {
        std::lock_guard<std::mutex> lock(mutex);
        decoding = active;
    }

    Snapshot take(int64_t job_id, const float* pcm, size_t count) {
        std::future<Snapshot> previous;
        {
            std::lock_guard<std::mutex> lock(mutex);
            decoding = false;
            previous = std::move(work);
        }
        if (!previous.valid()) return {};
        const auto start = Clock::now();
        auto snapshot = previous.get();
        snapshot.wait_s = std::chrono::duration<double>(Clock::now()-start).count();
        snapshot.matched = snapshot.job_id == job_id && snapshot.pcm.size() == count &&
                           std::memcmp(snapshot.pcm.data(), pcm, count*sizeof(float)) == 0;
        if (!snapshot.matched) snapshot.embedding.clear();
        return snapshot;
    }

private:
    std::mutex mutex;
    bool decoding = false;
    std::future<Snapshot> work;
};
