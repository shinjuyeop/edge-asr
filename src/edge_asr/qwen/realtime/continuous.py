"""최신 범위 하나를 실행 직전에 선택한다. 모든 상태는 작업자의 공통 condition으로 보호한다."""

import time
from threading import Event, Thread

from edge_asr import thermal


class ContinuousPolicy:
    def __init__(
        self,
        condition,
        emit,
        minimum,
        min_new,
        maximum,
        tail,
        *,
        clock=time.perf_counter,
        policy="latest",
        thermal_policy="pause",
    ):
        self.condition, self.emit, self.clock = condition, emit, clock
        self.minimum, self.min_new, self.maximum, self.tail = minimum, min_new, maximum, tail
        if policy not in ("latest", "paced") or thermal_policy not in ("pause", "pace"):
            raise ValueError("연속 정책은 latest/paced, 열 정책은 pause/pace여야 합니다.")
        self.policy, self.thermal_policy = policy, thermal_policy
        self.thermal_factor = 1.0
        self.overheated = False
        self.work_debt_s, self.accounted_at = 0.0, clock()
        self.wake_at = None
        self.candidate = None
        self.segment = None
        self.last_end = 0
        self.last_reason = None
        self.deferral = None
        self.blocked_since = None
        self.thermal_ready = False
        self.hot = False
        self.stop = Event()
        self.thread = None
        self.stats = {
            "mode": "continuous",
            "policy": policy,
            "version": "paced-completion-v2" if policy == "paced" else "latest-completion-v1",
            "thermal_policy": thermal_policy,
            "decisions": {},
            "candidate_updates": 0,
            "candidate_replacements": 0,
            "snapshots": 0,
            "snapshot_frames": 0,
            "discarded_candidates": 0,
            "pending_peak": 0,
            "thermal_backoff_s": 0.0,
            "thermal_pause_c": 82 if thermal_policy == "pause" else 90,
            "thermal_resume_c": 78 if thermal_policy == "pause" else 85,
            "thermal_pacing_start_c": 82 if thermal_policy == "pace" else None,
            "thermal_factor_peak": 1.0,
            "compute_duty_target": 0.7 if policy == "paced" else None,
            "first_debt_allowance_s": 0.35 if policy == "paced" else None,
            "final_reserve_max_s": 0.8 if policy == "paced" else tail / 16000,
            "accounted_inference_s": 0.0,
            "work_debt_peak_s": 0.0,
            "thermal_sample_s": 1.0,
            "tail_margin_s": tail / 16000,
            "deferred_s": {},
        }

    def _end_deferral(self):
        if self.deferral is not None:
            segment, reason, began = self.deferral
            duration = self.clock() - began
            totals = self.stats["deferred_s"]
            totals[reason] = totals.get(reason, 0.0) + duration
            self.emit(
                {
                    "type": "partial_deferral",
                    "segment_id": segment,
                    "reason": reason,
                    "duration_s": duration,
                    "at_monotonic_s": self.clock(),
                }
            )
            self.deferral = None

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
            self._end_deferral()
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

    def _account(self, now, work_s=0.0):
        rate = 0.7 / self.thermal_factor
        self.work_debt_s = max(
            0.0, self.work_debt_s + work_s - max(0.0, now - self.accounted_at) * rate
        )
        self.accounted_at = now
        self.stats["work_debt_peak_s"] = max(self.stats["work_debt_peak_s"], self.work_debt_s)
        return rate

    def completed(self, result):
        """실패·취소·final도 소비한 wall 비용에 포함한다. 작업자의 락 안에서 호출한다."""
        if self.policy == "paced":
            cost = result["ended_monotonic_s"] - result["started_monotonic_s"]
            self._account(self.clock(), cost)
            self.stats["accounted_inference_s"] += cost

    def wait_s(self):
        return max(0.001, self.wake_at - self.clock()) if self.wake_at is not None else None

    def choose(self, estimated_s=None):
        self.wake_at = None
        if self.candidate is None:
            return None
        segment, start, end, read = self.candidate
        now = self.clock()
        rate = self._account(now) if self.policy == "paced" else None
        first = self.last_end == start
        reserve = self.tail / 16000
        if self.policy == "paced" and estimated_s is not None:
            reserve = min(0.8, max(reserve, estimated_s))
        allowance = 0.35 if first else 0.0
        if end - start < self.minimum or (
            not first and end - self.last_end < self.min_new * self.thermal_factor
        ):
            reason = "minimum_audio"
        elif not self.thermal_ready or self.hot:
            reason = "thermal_backoff"
        elif (self.maximum - (end - start)) / 16000 <= reserve:
            # 긴 비용 추정이 발화 대부분을 막지 않도록 예약을 0.8초로 제한한다.
            reason = "near_final"
        elif self.policy == "paced" and self.work_debt_s > allowance + 1e-6:
            reason = "compute_pacing"
            self.wake_at = now + (self.work_debt_s - allowance) / rate
        else:
            reason = "offered"
        self.stats["decisions"][reason] = self.stats["decisions"].get(reason, 0) + 1
        key = (segment, reason)
        if key != self.last_reason or reason == "offered":
            self._end_deferral()
            if reason != "offered":
                self.deferral = (segment, reason, self.clock())
            self.emit(
                {
                    "type": "partial_decision",
                    "reason": reason,
                    "segment_id": segment,
                    "start_frame": start,
                    "end_frame": end,
                    "last_executed_end_frame": self.last_end,
                    "estimated_inference_s": estimated_s,
                    "final_reserve_s": reserve,
                    "thermal_factor": self.thermal_factor,
                    "work_debt_s": self.work_debt_s,
                    "compute_duty_target": rate,
                    "at_monotonic_s": self.clock(),
                }
            )
            self.last_reason = key
        if reason == "thermal_backoff" and not self.thermal_ready and self.blocked_since is None:
            self.blocked_since = self.clock()
            self.emit(
                {
                    "type": "thermal_backoff",
                    "state": "paused",
                    "reason": "first_sample_pending",
                    "at_monotonic_s": self.blocked_since,
                }
            )
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
            if self.thermal_policy == "pause":
                hot = unavailable or bool(flags) or temperature >= 82000
                if self.hot and not unavailable:
                    hot = bool(flags) or temperature > 78000
            else:
                if not unavailable:
                    self.overheated = temperature >= 90000 or (
                        self.overheated and temperature > 85000
                    )
                    factor = 1 + min(1, max(0, (temperature - 82000) / 3000)) * 0.4
                    # 현재 열 제한(2/4/8)은 완전 정지 대신 주기 조절에 사용한다.
                    self.thermal_factor = max(factor, 1.2 if flags & 14 else 1.0)
                    self.stats["thermal_factor_peak"] = max(
                        self.stats["thermal_factor_peak"], self.thermal_factor
                    )
                hot = unavailable or bool(flags & 1) or self.overheated
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
