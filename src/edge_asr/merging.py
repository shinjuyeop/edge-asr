"""정답을 사용하지 않는 원본 파일 ID 단위의 전사 선택 상태기계."""

import re
from collections import Counter
from dataclasses import dataclass, field

from edge_asr.metrics import normalize

POLICIES = ("zip_only", "refine_complete", "agreement_only")


@dataclass
class Source:
    id: str
    start: int
    end: int
    partial_end: int
    text: str = ""
    zip_final: str | None = None
    closed: bool = False
    sealed: bool = False
    jobs: dict = field(default_factory=dict)
    revision: int = 0
    decision: dict | None = None


class Merger:
    """부분 문자열을 시간으로 추정해 자르지 않는다. 닫힌 파일의 결과만 선택한다.

    파일 끝과 제출된 작업 수가 고정되기 전에는 Refine을 표시하지 않는다.
    finish는 호출자가 배출을 끝낸 명시적 시점이며 실제 서비스 timeout이 아니다.
    """

    def __init__(self, session_id, policy):
        if not isinstance(session_id, str) or not session_id or policy not in POLICIES:
            raise ValueError("세션 ID와 지원하는 병합 정책이 필요합니다.")
        self.session_id, self.policy = session_id, policy
        self.sources, self.job_sources = {}, {}
        self.updates, self.ignored = [], Counter()
        self.last_ns = -1

    def register(self, source_id, start, end):
        if (
            not isinstance(source_id, str)
            or not source_id
            or source_id in self.sources
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end
            or (self.sources and start < next(reversed(self.sources.values())).end)
        ):
            raise ValueError("고유 원본 ID와 순서가 있는 비중첩 프레임 구간이 필요합니다.")
        self.sources[source_id] = Source(source_id, start, end, start)

    def _ignore(self, reason):
        self.ignored[reason] += 1
        return {"accepted": False, "reason": reason}

    def _show(self, source, text, phase, model, reason, at):
        if phase == "partial" and text == source.text:
            return
        source.revision += 1
        update = {
            "session_id": self.session_id,
            "source_id": source.id,
            "start": source.start,
            "end": source.end,
            "at_ns": at,
            "revision": source.revision,
            "phase": phase,
            "model": model,
            "reason": reason,
            "text": text,
            "previous_text": source.text,
            "text_changed": text != source.text,
        }
        source.text = text
        self.updates.append(update)
        if phase == "final":
            source.sealed = True
            source.decision = dict(update)

    def _choose(self, source, at, *, force=False):
        if not source.closed or source.sealed:
            return
        model, text, reason = "zip", source.zip_final, "streaming_only"
        if self.policy != "zip_only":
            jobs = sorted(source.jobs.values(), key=lambda j: (j["start"], j["end"]))
            if not jobs:
                reason = "no_refine_jobs"
            elif any(not j["accepted"] for j in jobs):
                reason = "dropped_refine"
            elif any(j.get("status") == "failed" for j in jobs):
                reason = "failed_refine"
            elif any(j.get("status") != "success" for j in jobs):
                if not force:
                    return
                reason = "missing_refine"
            else:
                candidate = " ".join(j["text"] for j in jobs)
                if not normalize(candidate):
                    reason = "empty_refine"
                elif self.policy == "agreement_only" and normalize(candidate) != normalize(text):
                    reason = "normalized_disagreement"
                else:
                    model, text = "refine", candidate
                    reason = (
                        "normalized_agreement"
                        if self.policy == "agreement_only"
                        else "complete_refine"
                    )
        self._show(source, text, "final", model, reason, at)

    @staticmethod
    def _identity(event, source):
        a, b = event["start"], event["end"]
        c, d = event.get("context_start", a), event.get("context_end", b)
        sha = event["payload_sha256"]
        if (
            any(type(x) is not int for x in (a, b, c, d))
            or not source.start <= c <= a < b == d <= source.end
            or not isinstance(sha, str)
            or not re.fullmatch("[a-f0-9]{64}", sha)
        ):
            raise ValueError("원본 밖/역행하는 소유·문맥 좌표 또는 payload 해시입니다.")
        return a, b, c, d, sha

    def apply(self, event):
        if not isinstance(event, dict):
            raise ValueError("병합 이벤트는 객체여야 합니다.")
        if {"reference", "gold", "errors"} & event.keys():
            raise ValueError("정답/채점 필드를 병합기에 전달하지 않습니다.")
        if event.get("session_id") != self.session_id:
            return self._ignore("wrong_session")
        source = self.sources.get(event.get("source_id"))
        if source is None:
            return self._ignore("unknown_source")
        kind, at = event["type"], event["at_ns"]
        if type(at) is not int or at < 0 or at < self.last_ns:
            raise ValueError("병합 이벤트의 단조 시계 순서가 잘못됐습니다.")
        self.last_ns = at
        if kind not in ("partial", "job", "refine", "close"):
            raise ValueError("지원하지 않는 병합 이벤트입니다.")
        if kind == "refine" and self.job_sources.get(event["job_id"]) != source.id:
            return self._ignore("job_source_mismatch")
        if source.sealed:
            return self._ignore("source_sealed")
        if kind == "partial":
            end = event["end"]
            if (
                type(end) is not int
                or not source.start < end <= source.end
                or not isinstance(event["text"], str)
            ):
                raise ValueError("부분 전사의 프레임/문자열이 잘못됐습니다.")
            if source.closed or end < source.partial_end:
                return self._ignore("stale_partial")
            source.partial_end = end
            self._show(source, event["text"], "partial", "zip", "streaming_partial", at)
        elif kind == "job":
            if source.closed:
                return self._ignore("job_after_close")
            index = event["job_id"]
            if (
                type(index) is not int
                or index < 0
                or index in self.job_sources
                or type(event["accepted"]) is not bool
            ):
                raise ValueError("중복/잘못된 Refine 작업 ID입니다.")
            identity = self._identity(event, source)
            if any(
                max(identity[0], j["start"]) < min(identity[1], j["end"])
                for j in source.jobs.values()
            ):
                raise ValueError("Refine 소유 범위가 중복됐습니다.")
            source.jobs[index] = {
                "identity": identity,
                "start": identity[0],
                "end": identity[1],
                "accepted": event["accepted"],
            }
            self.job_sources[index] = source.id
        elif kind == "refine":
            job = source.jobs[event["job_id"]]
            if self._identity(event, source) != job["identity"]:
                raise ValueError("Refine 작업/결과의 좌표 또는 payload 해시 불일치")
            if not job["accepted"]:
                return self._ignore("dropped_job_result")
            if "status" in job:
                return self._ignore("duplicate_result")
            if event["status"] not in ("success", "failed") or (
                event["status"] == "success" and not isinstance(event.get("text"), str)
            ):
                raise ValueError("Refine 결과 상태/문자열이 잘못됐습니다.")
            job.update(status=event["status"], text=event.get("text"))
            self._choose(source, at)
        else:
            if source.closed:
                return self._ignore("duplicate_close")
            if event["end"] != source.end or not isinstance(event["text"], str):
                raise ValueError("원본 끝의 Zip 최종 전사가 필요합니다.")
            source.closed = True
            source.zip_final = event["text"]
            self._show(
                source, source.zip_final, "partial", "zip", "zip_final_waiting_selection", at
            )
            self._choose(source, at)
        return {"accepted": True, "reason": "processed"}

    def finish(self, at_ns):
        if type(at_ns) is not int or at_ns < self.last_ns:
            raise ValueError("배출 종료 시각이 역행했습니다.")
        if any(not s.closed for s in self.sources.values()):
            raise ValueError("Zip 원본 처리가 완료되지 않았습니다.")
        self.last_ns = at_ns
        for source in self.sources.values():
            self._choose(source, at_ns, force=True)
        return [dict(source.decision) for source in self.sources.values()]
