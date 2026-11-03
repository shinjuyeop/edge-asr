"""연속 Zip과 구간별 Refine을 분리해 표시한다. 자동 교체/문자열 병합은 없다."""

import json
import time
from threading import RLock

from edge_asr.artifacts import write_event
from edge_asr.realtime.vad import OnlineVadReplay

PREFIX = "EDGE_ASR_CAPTION "


def render(event):
    """제어 문자는 화면에서 escape하고 저장된 모델 원문은 그대로 둔다."""
    text = json.dumps(event.get("text", ""), ensure_ascii=False)[1:-1]
    source = json.dumps(event["source_id"], ensure_ascii=False)[1:-1]
    label = {
        "zip_partial": "Zip 임시",
        "zip_final": "Zip 최종",
        "refine_pending": "보정 대기",
        "refine_candidate": "보정 후보·자동 적용 안 함",
        "refine_failed": "보정 실패",
        "refine_dropped": "보정 입력 드롭",
    }[event["kind"]]
    span = ""
    if "source_start_s" in event:
        span = f" {event['source_start_s']:.3f}~{event['source_end_s']:.3f}초"
    return f"[{source}][{label}{span}] {text}"


class CaptionReplay(OnlineVadReplay):
    """기존 연속 입력/온라인 VAD/2작업 큐를 유지하는 터미널 MVP 이벤트 경로."""

    def __init__(self, *args, caption_log, **kwargs):
        if kwargs.get("policy") != "vad0":
            raise ValueError("자막 MVP는 기존 vad0 정책을 사용합니다.")
        super().__init__(*args, **kwargs)
        self.caption_log = caption_log
        self.lock = RLock()
        self.jobs = {}
        self.last_partial = {}
        self.event_count = 0
        self.observer = self._observe

    def _emit(self, kind, sid, at, **values):
        row = {
            "event_index": self.event_count,
            "kind": kind,
            "source_id": sid,
            "available_ns": at,
            "emitted_ns": time.perf_counter_ns(),
            **values,
        }
        write_event(self.caption_log, row)
        # thermal guard가 원시 console을 보존하며 부모 터미널에 전달한다.
        print(PREFIX + json.dumps(row, ensure_ascii=False, allow_nan=False), flush=True)
        self.event_count += 1

    def _observe(self, kind, stream, text, end, returned, source_index):
        source = self.sources[source_index]
        with self.lock:
            if kind == "partial" and (not text or self.last_partial.get(source["id"]) == text):
                return
            self.last_partial[source["id"]] = text
            self._emit(
                "zip_" + kind,
                source["id"],
                returned,
                text=text,
                input_end_s=(end - source["start"]) / 16000,
            )

    def offer_payload(self, payload, start, end, source, extra=None):
        # 등록을 반환 reader와 직렬화한다. 모델 추론 자체는 이 lock을 사용하지 않는다.
        with self.lock:
            job = super().offer_payload(payload, start, end, source, extra)
            job["accepted"] = job["type"] == "enqueue"
            self.jobs[job["index"]] = job
            self._emit(
                "refine_pending" if job["accepted"] else "refine_dropped",
                source["id"],
                job["at_ns"],
                job_id=job["index"],
                source_start_s=(start - source["start"]) / 16000,
                source_end_s=(end - source["start"]) / 16000,
            )

    def receive(self, result, received_ns):
        with self.lock:
            index = result["index"]
            job = self.jobs.get(index)
            keys = ("index", "source_id", "start", "end", "payload_float32_sha256")
            if job is None or not job["accepted"] or any(result.get(k) != job[k] for k in keys):
                raise ValueError("보정 반환 ID/좌표/payload 불일치")
            if "received_ns" in job:
                raise ValueError("중복 보정 반환")
            text = result.get("hypothesis")
            if result["status"] == "success" and not isinstance(text, str):
                raise TypeError("성공한 보정 결과는 문자열이어야 합니다.")
            job["received_ns"] = received_ns
            source = self.sources[job["source_index"]]
            self._emit(
                "refine_candidate" if result["status"] == "success" else "refine_failed",
                job["source_id"],
                received_ns,
                job_id=index,
                text=text or "",
                source_start_s=(job["start"] - source["start"]) / 16000,
                source_end_s=(job["end"] - source["start"]) / 16000,
                native_ended_ns=result["ended_ns"],
                error=result.get("error"),
            )

    def display_complete(self):
        return all(not j["accepted"] or "received_ns" in j for j in self.jobs.values())
