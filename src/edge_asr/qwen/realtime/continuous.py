"""최신 범위 하나를 실행 직전에 선택한다. 모든 상태는 작업자의 공통 condition으로 보호한다."""

import time
from threading import Event, Thread

from edge_asr import thermal


class ContinuousPolicy:
    def __init__(
        self, condition, emit, minimum, min_new, maximum, tail, *, clock=time.perf_counter
    ):
        self.condition, self.emit, self.clock = condition, emit, clock
        self.minimum, self.min_new, self.maximum, self.tail = minimum, min_new, maximum, tail
        self.candidate = None
        self.segment = None
        self.last_end = 0
        self.last_reason = None
        self.blocked_since = None
        self.thermal_ready = False
        self.hot = False
        self.stop = Event()
        self.thread = None
        self.stats = {
            "mode": "continuous",
            "policy": "latest",
            "version": "latest-completion-v1",
            "decisions": {},
            "candidate_updates": 0,
            "candidate_replacements": 0,
            "snapshots": 0,
            "snapshot_frames": 0,
            "discarded_candidates": 0,
            "pending_peak": 0,
            "thermal_backoff_s": 0.0,
            "thermal_pause_c": 82,
            "thermal_resume_c": 78,
            "thermal_sample_s": 1.0,
            "tail_margin_s": tail / 16000,
        }

    def update(self, segment, start, end, read):
        if self.segment != segment:
            self.discard("segment_change")
            self.segment, self.last_end = segment, start
        if end <= self.last_end:
            return
        if self.candidate:
            self.stats["candidate_replacements"] += 1
        self.candidate = (segment, start, end, read)
        self.stats["candidate_updates"] += 1
        self.stats["pending_peak"] = 1
        self.condition.notify_all()

    def discard(self, reason, segment=None):
        if self.candidate and (segment is None or self.candidate[0] == segment):
            sid, start, end, _ = self.candidate
            self.emit(
                {
                    "type": "partial_candidate_discarded",
                    "reason": reason,
                    "segment_id": sid,
                    "start_frame": start,
                    "end_frame": end,
                    "at_monotonic_s": self.clock(),
                }
            )
            self.stats["discarded_candidates"] += 1
            self.candidate = None

    def choose(self):
        if self.candidate is None:
            return None
        segment, start, end, read = self.candidate
        if end - start < self.minimum or (
            self.last_end > start and end - self.last_end < self.min_new
        ):
            reason = "minimum_audio"
        elif not self.thermal_ready or self.hot:
            reason = "thermal_backoff"
        elif self.maximum - (end - start) <= self.tail:
            # 실제 종료까지 최대 tail만 합친다. 전체 호출 예상 비용을 미리 예약하지 않는다.
            reason = "near_final"
        else:
            reason = "offered"
        self.stats["decisions"][reason] = self.stats["decisions"].get(reason, 0) + 1
        key = (segment, reason)
        if key != self.last_reason or reason == "offered":
            self.emit(
                {
                    "type": "partial_decision",
                    "reason": reason,
                    "segment_id": segment,
                    "start_frame": start,
                    "end_frame": end,
                    "last_executed_end_frame": self.last_end,
                    "at_monotonic_s": self.clock(),
                }
            )
            self.last_reason = key
        if reason != "offered":
            return None
        # 선택·제출·시작은 같은 condition 안에서 수행해 새 final이 끼어들지 않는다.
        self.last_end = end
        self.candidate = None
        self.stats["snapshots"] += 1
        self.stats["snapshot_frames"] += end - start
        return segment, start, end, read(start, end)

    def observe(self, row):
        """외부 표본은 락 밖에서 수집한다. 결측 시 부분 요청만 보류한다."""
        with self.condition:
            temperature, flags = row.get("temperature_mc"), row.get("current_limits")
            unavailable = temperature is None or flags is None
            hot = unavailable or bool(flags) or temperature >= 82000
            if self.hot and not unavailable:
                hot = bool(flags) or temperature > 78000
            now = self.clock()
            if hot and self.blocked_since is None:
                self.blocked_since = now
                self.emit(
                    {
                        "type": "thermal_backoff",
                        "state": "paused",
                        "reason": "telemetry_unavailable"
                        if unavailable
                        else "temperature_or_current_limit",
                        "at_monotonic_s": now,
                        **row,
                    }
                )
            elif not hot and self.blocked_since is not None:
                duration = now - self.blocked_since
                self.stats["thermal_backoff_s"] += duration
                self.emit(
                    {
                        "type": "thermal_backoff",
                        "state": "resumed",
                        "duration_s": duration,
                        "at_monotonic_s": now,
                        **row,
                    }
                )
                self.blocked_since = None
            self.hot, self.thermal_ready = hot, True
            self.condition.notify_all()

    def start(self):
        def monitor():
            while not self.stop.is_set():
                row = thermal.sample(measure_arm_clock=True)
                self.emit({"type": "partial_thermal_sample", **row})
                self.observe(row)
                if self.stop.wait(1):
                    return

        self.thread = Thread(target=monitor, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(4)
        with self.condition:
            self.discard("close")
            if self.blocked_since is not None:
                duration = self.clock() - self.blocked_since
                self.stats["thermal_backoff_s"] += duration
                self.emit(
                    {
                        "type": "thermal_backoff",
                        "state": "ended_while_paused",
                        "duration_s": duration,
                        "at_monotonic_s": self.clock(),
                    }
                )
                self.blocked_since = None
