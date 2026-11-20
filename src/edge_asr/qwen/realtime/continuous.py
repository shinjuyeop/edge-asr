"""최신 범위 하나를 실행 직전에 선택한다. 모든 상태는 작업자의 공통 condition으로 보호한다."""

import math
import time
from threading import Event, Thread

from edge_asr import thermal

POLICY_VERSIONS = {
    "latest": "latest-completion-v1",
    "paced": "paced-completion-v2",
    "headroom": "estimated-headroom-v1",
    "headroom_followup": "prefix-time-followup-v1",
}


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
        followup_gap_target_s=None,
    ):
        self.condition, self.emit, self.clock = condition, emit, clock
        self.minimum, self.min_new, self.maximum, self.tail = minimum, min_new, maximum, tail
        if policy not in POLICY_VERSIONS or thermal_policy not in ("pause", "pace"):
            raise ValueError("지원하지 않는 연속 갱신 또는 열 정책입니다.")
        self.policy, self.thermal_policy = policy, thermal_policy
        if followup_gap_target_s is not None and (
            policy != "headroom_followup"
            or not math.isfinite(followup_gap_target_s)
            or followup_gap_target_s <= 0
        ):
            raise ValueError("예상 공백 기준은 headroom_followup에서 유한한 양수로 지정합니다.")
        self.followup_gap_target_s = followup_gap_target_s
        self.last_display_end = -1
        self.last_display_at = None
        self.thermal_factor = 1.0
        self.overheated = False
        self.work_debt_s, self.accounted_at = 0.0, clock()
        self.wake_at = None
        self.candidate = None
        self.segment = None
        self.last_end = 0
        self.followup_used = False
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
            "version": POLICY_VERSIONS[policy],
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
        if policy in ("headroom", "headroom_followup"):
            self.stats["final_reserve_max_s"] = None
        if policy == "headroom_followup":
            self.stats["near_final_followups"] = 0

        if followup_gap_target_s is not None:
            self.stats.update(
                version="projected-gap-followup-v1", followup_gap_target_s=followup_gap_target_s
            )

    def record_display(self, event, previous, t0):
        """표시 내용과 PCM 끝이 함께 전진한 서버 발행 시각만 보존한다. 세션 락 안에서 호출."""
        if (
            self.followup_gap_target_s is None
            or event["end_frame"] <= self.last_display_end
            or not event["displayed"]
            or event["status"] == "failed"
            or not (event["hypothesis"] or "").strip()
            or (
                previous
                and event["segment_id"] == previous["segment_id"]
                and event["hypothesis"] == previous["hypothesis"]
            )
        ):
            return
        self.last_display_end = event["end_frame"]
        self.last_display_at = t0 + event["published_s"]

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
            self.followup_used = False
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

    def _prefix_time(self, previous, start, end):
        """직전 표시를 다시 생성하고 다음 토큰을 선택할 때까지의 추정 시간."""
        if (
            not previous
            or previous["kind"] != "partial"
            or previous["status"] != "success"
            or not previous["displayed"]
            or previous["segment_id"] != self.segment
            or previous["start_frame"] != start
            or previous["end_frame"] != self.last_end
        ):
            return None
        native = (previous.get("adapter_result") or {}).get("native_result") or {}
        keys = (
            "first_generated_token_s",
            "token_decode_s",
            "audio_encode_s",
            "audio_embedding_eval_s",
        )
        stages = [native.get(key) for key in keys]
        if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in stages):
            return None
        first, decode, encode, embeddings = stages
        # 직렬 부분 호출은 마지막 텍스트 토큰 뒤의 EOG 선택까지 decode한다.
        # 이전 토큰열을 재생한다고 가정하고, 새로 늘어난 PCM의 음성 단계 비용을 더한다.
        return (
            first + decode + (encode + embeddings) * (end - self.last_end) / (self.last_end - start)
        )

    def choose(self, estimated_s=None, *, previous=None, available_frame=None):
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
        elif self.policy in ("headroom", "headroom_followup"):
            # 이력이 없을 때는 한 번의 새 음성 간격을 확보한다.
            reserve += self.min_new / 16000 if estimated_s is None else estimated_s
        allowance = 0.35 if first else 0.0
        prefix_time = None
        followup = False
        display_age = projected_gap = None
        remaining = (self.maximum - (end - start)) / 16000
        if end - start < self.minimum or (
            not first and end - self.last_end < self.min_new * self.thermal_factor
        ):
            reason = "minimum_audio"
        elif not self.thermal_ready or self.hot:
            reason = "thermal_backoff"
        elif remaining <= reserve:
            reason = "near_final"
            if self.policy == "headroom_followup" and not first and not self.followup_used:
                prefix_time = self._prefix_time(previous, start, end)
                # VAD가 넘긴 PCM 끝보다 실제 입력이 앞섰다면 그만큼 대기 여유를 줄인다.
                remaining = (start + self.maximum - max(end, available_frame or end)) / 16000
                if prefix_time is not None and prefix_time + self.tail / 16000 < remaining:
                    if self.followup_gap_target_s is not None and self.last_display_at is not None:
                        display_age = max(0.0, now - self.last_display_at)
                        projected_gap = display_age + remaining + prefix_time
                    followup = self.followup_gap_target_s is None or (
                        projected_gap is not None and projected_gap >= self.followup_gap_target_s
                    )
                    reason = "offered" if followup else "projected_gap_below_target"
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
                    **(
                        {"display_age_s": display_age, "projected_gap_s": projected_gap}
                        if self.followup_gap_target_s is not None
                        else {}
                    ),
                    "estimated_inference_s": estimated_s,
                    "final_reserve_s": reserve,
                    "thermal_factor": self.thermal_factor,
                    "work_debt_s": self.work_debt_s,
                    "compute_duty_target": rate,
                    "at_monotonic_s": self.clock(),
                    **(
                        {
                            "near_final_followup": followup,
                            "prefix_time_s": prefix_time,
                            "remaining_s": remaining,
                        }
                        if self.policy == "headroom_followup"
                        else {}
                    ),
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
        if followup:
            self.followup_used = True
            self.stats["near_final_followups"] += 1
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
