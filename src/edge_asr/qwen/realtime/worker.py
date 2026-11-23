"""모델 하나를 직렬 호출한다. 최종 구간은 유한 FIFO, 대기 부분 전사는 최신 하나만 유지한다."""

import math
import time
from collections import deque
from copy import deepcopy
from threading import Condition, Thread

from edge_asr.dataset import Audio, float_digest
from edge_asr.runtime import InferenceCancelled


class DecodeWorker:
    def __init__(
        self,
        adapter,
        emit,
        *,
        capacity=4,
        token_stream=False,
        condition=None,
        next_partial=None,
        cancel_partial_for_final=False,
        cancel_partial_grace_s=0.0,
        on_completed=None,
    ):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("최종 구간 큐 용량은 양의 정수여야 합니다.")
        if type(token_stream) is not bool or (
            token_stream and getattr(adapter, "supports_token_stream", False) is not True
        ):
            raise ValueError("토큰 배포에는 지원하는 GGUF adapter가 필요합니다.")
        self.token_stream = token_stream
        self.adapter, self.emit, self.capacity = adapter, emit, capacity
        self.condition = condition if condition is not None else Condition()
        self.next_partial = next_partial
        self.cancel_partial_for_final = cancel_partial_for_final
        self.cancel_partial_grace_s = cancel_partial_grace_s
        self.on_completed = on_completed
        self.finals, self.partial = deque(), None
        self.closing, self.error = False, None
        self.active, self.last_inference_s = False, 0.0
        self.costs = {kind: deque(maxlen=3) for kind in ("partial", "final")}
        self.index = self.queue_peak = 0
        self.cache_segment = None
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def availability(self):
        with self.condition:
            idle = not (self.active or self.finals or self.partial or self.closing or self.error)
            return idle, self.last_inference_s

    def estimate_cost(self, kind, frames, *, model="total"):
        """최근3회·1.2 여유. audio_scaled는 짧아진 입력의 음성 비용도 비례 조정한다."""
        if model not in ("total", "audio", "audio_scaled"):
            raise ValueError("비용 추정은 total/audio/audio_scaled여야 합니다.")
        with self.condition:
            history = self.costs[kind]
            if not history:
                return None
            if model in ("audio", "audio_scaled") and all(
                audio is not None for _, _, audio in history
            ):
                return (
                    max(
                        cost
                        + audio
                        * (
                            (
                                frames / samples
                                if model == "audio_scaled"
                                else max(1.0, frames / samples)
                            )
                            - 1
                        )
                        for samples, cost, audio in history
                    )
                    * 1.2
                )
            # 단계 계측이 없거나 실패한 호출을 포함하면 기존 추정으로 돌아간다.
            largest_input = max(samples for samples, _, _ in history)
            return max(cost for _, cost, _ in history) * max(1.0, frames / largest_input) * 1.2

    def submit(
        self,
        kind,
        segment_id,
        start,
        end,
        samples,
        available_frame,
        *,
        only_if_idle=False,
        owned_start_frame=None,
    ):
        with self.condition:
            if self.closing or self.error or not self.thread.is_alive():
                raise RuntimeError(self.error or "종료된 전사 작업자입니다.")
            if kind not in ("partial", "final") or not 0 <= start < end <= available_frame:
                raise ValueError("도착한 음성 범위의 partial/final 작업이 필요합니다.")
            if owned_start_frame is not None and not start <= owned_start_frame < end:
                raise ValueError("소유 구간은 입력 PCM 안에 있어야 합니다.")
            if end - start != len(samples):
                raise ValueError("전사 구간과 PCM 길이가 다릅니다.")
            if only_if_idle and (self.active or self.finals or self.partial is not None):
                return False
            if kind == "final" and len(self.finals) >= self.capacity:
                raise RuntimeError("final_queue_full: 입력을 늦추거나 최종 구간을 버리지 않습니다.")
            job = {
                "job_id": self.index,
                "revision": self.index,
                "kind": kind,
                "segment_id": segment_id,
                "start_frame": start,
                "end_frame": end,
                "available_frame": available_frame,
                **(
                    {"owned_start_frame": owned_start_frame}
                    if owned_start_frame is not None
                    else {}
                ),
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
                if (
                    self.active
                    and not self.finals
                    and getattr(self.adapter, "prepare_final_audio", False)
                ):
                    try:
                        job["audio_prepare_offered"] = self.adapter.offer_final_audio(
                            job["job_id"], samples
                        )
                    except Exception as error:
                        job["audio_prepare_offered"] = False
                        job["audio_prepare_error"] = str(error)
                self.finals.append((job, samples))
            else:
                self.partial = (job, samples)
            self.queue_peak = max(self.queue_peak, len(self.finals) + (self.partial is not None))
            self.emit({"type": "submitted", **job})
            self.condition.notify()
            return True

    def _run(self):
        try:
            while True:
                with self.condition:
                    while not (self.finals or self.partial or self.closing):
                        wait_s = None
                        if self.next_partial:
                            wait_s = self.next_partial()
                        if not self.partial:
                            self.condition.wait(timeout=wait_s)
                    if self.finals:
                        job, samples = self.finals.popleft()
                    elif self.partial is not None:
                        job, samples = self.partial
                        self.partial = None
                    else:
                        return
                    self.active = True
                started = time.perf_counter()
                self.emit({"type": "started", **job, "started_monotonic_s": started})
                result = {"type": "result", **job, "status": "failed", "hypothesis": None}
                reset_cache = None
                try:
                    reset_cache = getattr(self.adapter, "reset_audio_feature_cache", None)
                    if reset_cache and self.cache_segment != job["segment_id"]:
                        reset_cache()
                        self.cache_segment = job["segment_id"]
                    audio = Audio(samples, 16000, job["payload_float32_sha256"])
                    prepare_request = getattr(self.adapter, "prepare_realtime_request", None)
                    if prepare_request:
                        prepare_request(job["kind"])
                    prepare_job = getattr(self.adapter, "prepare_realtime_job", None)
                    if prepare_job:
                        prepare_job(deepcopy(job))

                    def token(update):
                        with self.condition:
                            if (
                                self.cancel_partial_for_final
                                and job["kind"] == "partial"
                                and self.finals
                                and time.perf_counter() - self.finals[0][0]["submitted_monotonic_s"]
                                >= self.cancel_partial_grace_s
                            ):
                                self.emit(
                                    {
                                        "type": "partial_cancellation_requested",
                                        **job,
                                        "reason": "final_pending",
                                        "at_monotonic_s": time.perf_counter(),
                                    }
                                )
                                raise InferenceCancelled("final_pending")
                        self.emit(
                            {
                                "type": "token",
                                **job,
                                "status": "provisional",
                                "started_monotonic_s": started,
                                "received_monotonic_s": time.perf_counter(),
                                **update,
                            }
                        )

                    text = (
                        self.adapter.transcribe_stream(audio, token)
                        if self.token_stream
                        else self.adapter.transcribe(audio)
                    )
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError("음성 구간에서 비어 있지 않은 문자열이 필요합니다.")
                    result.update(status="success", hypothesis=text)
                except InferenceCancelled as error:
                    result.update(status="cancelled", cancellation_reason=str(error))
                    result["error"] = {"type": type(error).__name__, "message": str(error)}
                except Exception as error:
                    result["error"] = {"type": type(error).__name__, "message": str(error)}
                result.update(
                    started_monotonic_s=started,
                    ended_monotonic_s=time.perf_counter(),
                    adapter_result=deepcopy(getattr(self.adapter, "last_result_metadata", None)),
                )
                if (
                    result["status"] == "failed"
                    and job["kind"] == "partial"
                    and result.get("error", {}).get("message")
                    == "음성 구간에서 비어 있지 않은 문자열이 필요합니다."
                ):
                    result["failure_kind"] = "empty_partial"
                try:
                    self.emit(result)
                finally:
                    if reset_cache and (job["kind"] == "final" or result["status"] != "success"):
                        reset_cache()
                        self.cache_segment = None
                    with self.condition:
                        self.last_inference_s = result["ended_monotonic_s"] - started
                        native = (result["adapter_result"] or {}).get("native_result") or {}
                        stages = [
                            native.get(key) for key in ("audio_encode_s", "audio_embedding_eval_s")
                        ]
                        audio = None
                        if result["status"] == "success" and all(
                            type(value) in (int, float) and math.isfinite(value) and value >= 0
                            for value in stages
                        ):
                            measured = sum(stages)
                            if measured <= self.last_inference_s:
                                audio = measured
                        self.costs[job["kind"]].append((len(samples), self.last_inference_s, audio))
                        if self.on_completed:
                            self.on_completed(result)
                        self.active = False
                        self.condition.notify_all()
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
