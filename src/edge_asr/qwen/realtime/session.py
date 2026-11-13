"""공통 VAD·유한 PCM 버퍼에서 도착한 접두 음성만 비동기 전사한다."""

import hashlib
import math
import time
from array import array
from threading import RLock

from edge_asr.dataset import pcm_bytes
from edge_asr.qwen.realtime.stability import replays_prefix
from edge_asr.qwen.realtime.worker import DecodeWorker
from edge_asr.segmentation import SegmentPipeline


class CaptionSession:
    def __init__(
        self,
        adapter,
        vad,
        frames,
        emit,
        telemetry,
        *,
        partial_after_s=2,
        partial_interval_s=1,
        partial_followup_s=None,
        partial_mode="fixed",
        partial_min_new_s=0.8,
        partial_tail_margin_s=0.2,
        adaptive_policy="recent",
        partial_cost_model="total",
        partial_budget_s=2.5,
        partial_max_calls=2,
        max_segment_s=5,
        drain_timeout_s=30,
        queue_capacity=4,
        partials=True,
        silence_policy="skip-zero",
        vad_preprocess="identity",
        token_stream=False,
    ):
        if partial_after_s <= 0 or partial_interval_s <= 0:
            raise ValueError("부분 전사 시작·갱신 간격은 양수여야 합니다.")
        if partial_followup_s is not None and (
            not math.isfinite(partial_followup_s) or partial_followup_s <= 0
        ):
            raise ValueError("첫 후속 갱신 간격은 유한한 양수여야 합니다.")
        if type(partials) is not bool or silence_policy not in ("preserve", "skip-zero"):
            raise ValueError("부분 전사 bool·무음 preserve/skip-zero 정책이 필요합니다.")
        if partial_mode not in ("fixed", "adaptive") or (
            partial_mode == "adaptive" and partial_followup_s is not None
        ):
            raise ValueError(
                "부분 일정은 fixed/adaptive이며 adaptive는 첫 후속 옵션과 함께 쓰지 않습니다."
            )
        if (
            not math.isfinite(partial_min_new_s)
            or round(partial_min_new_s * 16000) < 1
            or not math.isfinite(partial_tail_margin_s)
            or partial_tail_margin_s < 0
        ):
            raise ValueError(
                "새 음성 최소량은 1프레임 이상, 끝 여유는 유한한 음수가 아닌 값이어야 합니다."
            )
        self.partial_mode = partial_mode
        if adaptive_policy not in ("recent", "budgeted") or (
            adaptive_policy == "budgeted" and partial_mode != "adaptive"
        ):
            raise ValueError("budgeted 비용 정책은 adaptive 모드에서만 사용합니다.")
        if (
            not math.isfinite(partial_budget_s)
            or partial_budget_s <= 0
            or type(partial_max_calls) is not int
            or partial_max_calls < 1
        ):
            raise ValueError("부분 비용 예산은 유한한 양수, 요청 상한은 양의 정수여야 합니다.")
        self.adaptive_policy = adaptive_policy
        if partial_cost_model not in ("total", "audio") or (
            partial_cost_model == "audio" and adaptive_policy != "budgeted"
        ):
            raise ValueError("audio 비용 추정은 budgeted 정책에서만 사용합니다.")
        self.partial_cost_model = partial_cost_model
        self.partial_budget_s, self.partial_max_calls = partial_budget_s, partial_max_calls
        self.min_new_frames = round(partial_min_new_s * 16000)
        self.tail_margin_s = partial_tail_margin_s
        self.scheduler_stats = {
            "mode": partial_mode,
            "policy": adaptive_policy,
            "version": "budgeted-headroom-v2" if adaptive_policy == "budgeted" else "recent-v1",
            "cost_model": partial_cost_model,
            "decisions": {},
        }
        self.last_decision = None
        self.partials, self.silence_policy = partials, silence_policy
        self.frames, self.emit, self.telemetry = frames, emit, telemetry
        self.minimum, self.interval = (
            round(partial_after_s * 16000),
            round(partial_interval_s * 16000),
        )
        self.followup = (
            self.interval if partial_followup_s is None else round(partial_followup_s * 16000)
        )
        self.partial_requests = 0
        self.timeout = drain_timeout_s
        self.position = 0
        self.partial_start, self.last_partial_end = None, 0
        self.lock = RLock()
        self.results, self.segments, self.finals = [], [], {}
        self.skipped, self.finalized = [], set()
        self.partial = None
        self.token_timings = {}
        self.pipeline = SegmentPipeline(
            vad,
            "identity",
            max_segment_s,
            self._final,
            context_frames=0,
            native_budget_frames=None,
            progress=self._progress,
            vad_policy=vad_preprocess,
        )
        self.worker = DecodeWorker(
            adapter, self._event, capacity=queue_capacity, token_stream=token_stream
        )

    def _event(self, event):
        if event["type"] == "token":
            self._token(event)
        if event["type"] == "result":
            t0 = self.telemetry["t0_monotonic_ns"] / 1e9
            with self.lock:
                event["published_s"] = time.perf_counter() - t0
                event["queue_wait_s"] = (
                    event["started_monotonic_s"] - event["submitted_monotonic_s"]
                )
                event["inference_s"] = event["ended_monotonic_s"] - event["started_monotonic_s"]
                self.results.append(event)
                event["displayed"] = (
                    event["kind"] == "final" or event["segment_id"] not in self.finalized
                )
                if event["kind"] == "final":
                    self.finalized.add(event["segment_id"])
                    self.finals[event["segment_id"]] = event
                    if self.partial and self.partial["segment_id"] <= event["segment_id"]:
                        self.partial = None
                elif event["segment_id"] not in self.finalized:
                    self.partial = event
        self.emit(event)

    def _token(self, event):
        t0 = self.telemetry["t0_monotonic_ns"] / 1e9
        with self.lock:
            event["published_s"] = time.perf_counter() - t0
            timing = self.token_timings.setdefault(
                event["job_id"],
                {
                    key: event[key]
                    for key in ("job_id", "kind", "segment_id", "start_frame", "end_frame")
                }
                | {
                    "first_received_from_call_start_s": event["received_monotonic_s"]
                    - event["started_monotonic_s"],
                    "first_native_elapsed_s": event["native_elapsed_s"],
                    "first_visible_published_s": None,
                    "first_visible_from_call_start_s": None,
                    "token_events": 0,
                    "visible_events": 0,
                    "visible_characters": 0,
                },
            )
            timing["token_events"] += 1
            text = event["hypothesis"]
            event["prefix_replay_suppressed"] = replays_prefix(self.partial, event)
            event["displayed"] = (
                bool(text.strip())
                and len(text) > timing["visible_characters"]
                and event["segment_id"] not in self.finalized
                and not event["prefix_replay_suppressed"]
            )
            if event["displayed"]:
                if timing["first_visible_published_s"] is None:
                    timing["first_visible_published_s"] = event["published_s"]
                    timing["first_visible_from_call_start_s"] = (
                        event["published_s"] + t0 - event["started_monotonic_s"]
                    )
                timing["visible_events"] += 1
                timing["visible_characters"] = len(text)
                self.partial = event

    def _offer(self, kind, segment_id, start, end, pcm, *, only_if_idle=False):
        if not 0 <= start < end <= self.position or end - start != len(pcm):
            raise ValueError("도착한 PCM과 전사 구간이 일치해야 합니다.")
        if self.silence_policy == "skip-zero" and not any(pcm):
            row = {
                "type": "skipped_silence",
                "kind": kind,
                "segment_id": segment_id,
                "start_frame": start,
                "end_frame": end,
                "available_frame": self.position,
                "pcm16_sha256": hashlib.sha256(pcm_bytes(pcm)).hexdigest(),
                "reason": "exact_zero_pcm",
                "at_monotonic_s": time.perf_counter(),
            }
            with self.lock:
                self.skipped.append(row)
                if kind == "final":
                    self.finalized.add(segment_id)
                    if self.partial and self.partial["segment_id"] == segment_id:
                        self.partial = None
            self.emit(row)
            return True
        return self.worker.submit(
            kind,
            segment_id,
            start,
            end,
            array("f", (x / 32768 for x in pcm)),
            self.position,
            only_if_idle=only_if_idle,
        )

    def _request_partial(self, start, end, read, *, only_if_idle):
        if not self._offer(
            "partial",
            self.pipeline.segment_id,
            start,
            end,
            read(start, end),
            only_if_idle=only_if_idle,
        ):
            return False
        self.last_partial_end = end
        self.partial_requests += 1
        return True

    def _adaptive_progress(self, start, end, read):
        idle, last_cost = self.worker.availability()
        remaining = (self.pipeline.maximum - (end - start)) / 16000
        protected = self.adaptive_policy == "budgeted"
        estimate, final_cost, spent, budget = None, None, 0.0, self.partial_budget_s
        if protected and self.partial_requests:
            estimate = self.worker.estimate_cost(
                "partial", end - start, model=self.partial_cost_model
            )
            final_cost = self.worker.estimate_cost("final", self.pipeline.maximum)
            with self.lock:
                spent = sum(
                    r["inference_s"]
                    for r in self.results
                    if r["kind"] == "partial" and r["segment_id"] == self.pipeline.segment_id
                )
        if end - start < self.minimum or (
            self.partial_requests and end - self.last_partial_end < self.min_new_frames
        ):
            reason = "minimum_audio"
        elif protected and not self.partial_requests:
            # 최초 접두 PCM은 고정 기준과 같이 한 번 대기시킨다. 최종 도착 시 기존 합침 유지.
            self._request_partial(start, end, read, only_if_idle=False)
            reason = "first_offered"
        elif not idle:
            reason = "busy"
        elif protected and self.partial_requests >= self.partial_max_calls:
            reason = "call_budget"
        elif protected and estimate is None:
            reason = "cost_unavailable"
        elif protected and spent + estimate > budget:
            reason = "cost_budget"
        elif remaining <= (estimate if protected else last_cost) + self.tail_margin_s:
            reason = "near_final"
        elif self._request_partial(start, end, read, only_if_idle=True):
            reason = "offered"
        else:
            reason = "busy"
        counts = self.scheduler_stats["decisions"]
        counts[reason] = counts.get(reason, 0) + 1
        if reason != self.last_decision or reason in ("offered", "first_offered"):
            self.emit(
                {
                    "type": "partial_decision",
                    "reason": reason,
                    "segment_id": self.pipeline.segment_id,
                    "start_frame": start,
                    "end_frame": end,
                    "available_frame": self.position,
                    "last_inference_s": last_cost,
                    "estimated_partial_s": estimate,
                    "partial_cost_model": self.partial_cost_model,
                    "estimated_final_s": final_cost,
                    "partial_spent_s": spent,
                    "partial_budget_s": budget,
                    "remaining_to_cap_s": remaining,
                    "at_monotonic_s": time.perf_counter(),
                }
            )
            self.last_decision = reason

    def _progress(self, start, end, read):
        if not self.partials:
            return
        if self.partial_start != start:
            self.partial_start, self.last_partial_end = start, start
            self.partial_requests = 0
            self.last_decision = None
        if self.partial_mode == "adaptive":
            self._adaptive_progress(start, end, read)
            return
        interval = self.followup if self.partial_requests == 1 else self.interval
        if end - start >= self.minimum and (
            self.last_partial_end == start or end - self.last_partial_end >= interval
        ):
            self._request_partial(start, end, read, only_if_idle=False)

    def _final(self, event, pcm):
        self.segments.append(dict(event))
        self._offer(
            "final", event["segment_id"], event["owned_start_frame"], event["owned_end_frame"], pcm
        )

    def start_stream(self):
        return self

    def accept_at(self, stream, samples, start, end):
        if stream is not self or start != self.position or end - start != len(samples):
            raise RuntimeError("capture_gap: 손실 음성을 이어 붙이지 않습니다.")
        if self.worker.error:
            raise RuntimeError(self.worker.error)
        self.position = end
        self.pipeline.accept(
            samples, final=end == self.frames or (self.frames is None and len(samples) < 1600)
        )
        return self.text()

    def finish_stream(self, stream):
        if stream is not self or (self.frames is not None and self.position != self.frames):
            raise RuntimeError("입력이 완전하지 않습니다.")
        self.pipeline.finish()
        self.worker.finish(self.timeout)
        return self.text(final_only=True)

    def text(self, *, final_only=False):
        with self.lock:
            parts = [row["hypothesis"] or "" for _, row in sorted(self.finals.items())]
            if (
                not final_only
                and self.partial
                and self.partial["status"] in ("success", "provisional")
            ):
                parts.append(self.partial["hypothesis"])
            return " ".join(part for part in parts if part)

    def close(self):
        self.worker.abort()
