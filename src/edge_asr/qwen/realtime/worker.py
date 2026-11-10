"""모델 하나를 직렬 호출한다. 최종 구간은 유한 FIFO, 대기 부분 전사는 최신 하나만 유지한다."""

import time
from collections import deque
from copy import deepcopy
from threading import Condition, Thread

from edge_asr.dataset import Audio, float_digest


class DecodeWorker:
    def __init__(self, adapter, emit, *, capacity=4):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("최종 구간 큐 용량은 양의 정수여야 합니다.")
        self.adapter, self.emit, self.capacity = adapter, emit, capacity
        self.condition = Condition()
        self.finals, self.partial = deque(), None
        self.closing, self.error = False, None
        self.index = self.queue_peak = 0
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, kind, segment_id, start, end, samples, available_frame):
        with self.condition:
            if self.closing or self.error or not self.thread.is_alive():
                raise RuntimeError(self.error or "종료된 전사 작업자입니다.")
            if kind not in ("partial", "final") or not 0 <= start < end <= available_frame:
                raise ValueError("도착한 음성 범위의 partial/final 작업이 필요합니다.")
            if end - start != len(samples):
                raise ValueError("전사 구간과 PCM 길이가 다릅니다.")
            if kind == "final" and len(self.finals) >= self.capacity:
                raise RuntimeError("final_queue_full: 입력을 늦추거나 최종 구간을 버리지 않습니다.")
            job = {
                "job_id": self.index,
                "kind": kind,
                "segment_id": segment_id,
                "start_frame": start,
                "end_frame": end,
                "available_frame": available_frame,
                "submitted_monotonic_s": time.perf_counter(),
                "payload_float32_sha256": float_digest(samples),
            }
            self.index += 1
            if self.partial is not None and (
                kind == "partial" or self.partial[0]["segment_id"] == segment_id
            ):
                self.emit({"type": "coalesced", **self.partial[0], "replaced_by": job["job_id"]})
                self.partial = None
            if kind == "final":
                self.finals.append((job, samples))
            else:
                self.partial = (job, samples)
            self.queue_peak = max(self.queue_peak, len(self.finals) + (self.partial is not None))
            self.emit({"type": "submitted", **job})
            self.condition.notify()

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.finals or self.partial or self.closing)
                    if self.finals:
                        job, samples = self.finals.popleft()
                    elif self.partial is not None:
                        job, samples = self.partial
                        self.partial = None
                    else:
                        return
                started = time.perf_counter()
                self.emit({"type": "started", **job, "started_monotonic_s": started})
                result = {"type": "result", **job, "status": "failed", "hypothesis": None}
                try:
                    text = self.adapter.transcribe(
                        Audio(samples, 16000, job["payload_float32_sha256"])
                    )
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("음성 구간에서 비어 있지 않은 문자열이 필요합니다.")
                    result.update(status="success", hypothesis=text)
                except Exception as error:
                    result["error"] = {"type": type(error).__name__, "message": str(error)}
                result.update(
                    started_monotonic_s=started,
                    ended_monotonic_s=time.perf_counter(),
                    adapter_result=deepcopy(getattr(self.adapter, "last_result_metadata", None)),
                )
                self.emit(result)
        except BaseException as error:
            self.error = f"{type(error).__name__}: {error}"

    def finish(self, timeout):
        with self.condition:
            self.closing = True
            if self.partial is not None:
                self.emit({"type": "coalesced", **self.partial[0], "replaced_by": "eof"})
                self.partial = None
            self.condition.notify()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError("전사 작업자 배출 제한 초과")
        if self.error:
            raise RuntimeError(self.error)

    def abort(self):
        with self.condition:
            for job, _ in [*self.finals, *([self.partial] if self.partial is not None else [])]:
                self.emit({"type": "cancelled", **job})
            self.finals.clear()
            self.partial = None
            self.closing = True
            self.condition.notify()
        self.thread.join(1)
