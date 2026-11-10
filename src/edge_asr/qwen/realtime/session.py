"""공통 VAD·유한 PCM 버퍼에서 도착한 접두 음성만 비동기 전사한다."""

import hashlib
import time
from array import array
from threading import RLock

from edge_asr.dataset import pcm_bytes
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
        max_segment_s=5,
        drain_timeout_s=30,
        queue_capacity=4,
        partials=True,
        silence_policy="preserve",
    ):
        if partial_after_s <= 0 or partial_interval_s <= 0:
            raise ValueError("부분 전사 시작·갱신 간격은 양수여야 합니다.")
        if type(partials) is not bool or silence_policy not in ("preserve", "skip-zero"):
            raise ValueError("부분 전사 bool·무음 preserve/skip-zero 정책이 필요합니다.")
        self.partials, self.silence_policy = partials, silence_policy
        self.frames, self.emit, self.telemetry = frames, emit, telemetry
        self.minimum, self.interval = (
            round(partial_after_s * 16000),
            round(partial_interval_s * 16000),
        )
        self.timeout = drain_timeout_s
        self.position = 0
        self.partial_start, self.last_partial_end = None, 0
        self.lock = RLock()
        self.results, self.segments, self.finals = [], [], {}
        self.skipped, self.finalized = [], set()
        self.partial = None
        self.pipeline = SegmentPipeline(
            vad,
            "identity",
            max_segment_s,
            self._final,
            context_frames=0,
            native_budget_frames=None,
            progress=self._progress,
        )
        self.worker = DecodeWorker(adapter, self._event, capacity=queue_capacity)

    def _event(self, event):
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

    def _offer(self, kind, segment_id, start, end, pcm):
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
            return
        self.worker.submit(
            kind, segment_id, start, end, array("f", (x / 32768 for x in pcm)), self.position
        )

    def _progress(self, start, end, read):
        if not self.partials:
            return
        if self.partial_start != start:
            self.partial_start, self.last_partial_end = start, start
        if end - start >= self.minimum and end - self.last_partial_end >= self.interval:
            self._offer("partial", self.pipeline.segment_id, start, end, read(start, end))
            self.last_partial_end = end

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
        self.pipeline.accept(samples, final=end == self.frames)
        return self.text()

    def finish_stream(self, stream):
        if stream is not self or self.position != self.frames:
            raise RuntimeError("입력이 완전하지 않습니다.")
        self.pipeline.finish()
        self.worker.finish(self.timeout)
        return self.text(final_only=True)

    def text(self, *, final_only=False):
        with self.lock:
            parts = [row["hypothesis"] or "" for _, row in sorted(self.finals.items())]
            if not final_only and self.partial and self.partial["status"] == "success":
                parts.append(self.partial["hypothesis"])
            return " ".join(part for part in parts if part)

    def close(self):
        self.worker.abort()
