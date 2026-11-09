"""연속 Zip 토큰/프레임의 구조 진단. 실제 텍스트를 교체하거나 음향 정렬을 확정하지 않는다."""

import math
import re
import time
from bisect import bisect_left
from threading import RLock

from edge_asr.artifacts import write_event
from edge_asr.legacy.realtime.vad import OnlineVadReplay


def _cjk(c):
    cp = ord(c)
    return any(
        a <= cp <= b
        for a, b in (
            (0x1100, 0x11FF),
            (0x2E80, 0xA4CF),
            (0xA840, 0xD7AF),
            (0xF900, 0xFAFF),
            (0xFE30, 0xFE4F),
            (0xFF65, 0xFFDC),
            (0x20000, 0x2FFFF),
        )
    )


def _punct(c):
    cp = ord(c)
    return any(
        a <= cp <= b
        for a, b in (
            (0x21, 0x2F),
            (0x3A, 0x40),
            (0x5B, 0x60),
            (0x7B, 0x7E),
            (0x3000, 0x303F),
            (0xFF01, 0xFF0F),
            (0xFF1A, 0xFF20),
            (0xFF3B, 0xFF40),
            (0xFF5B, 0xFF65),
        )
    )


def render_tokens(tokens):
    """현재 Unicode BPE 범위의 upstream 표시 규칙. byte fallback/다른 변환은 거부한다.

    반환 origin은 각 표시 문자를 만든 토큰 인덱스다. 음성 시간/정답 정렬이 아니다.
    """
    if not isinstance(tokens, list) or any(
        not isinstance(t, str) or not t or "▁" in t or re.fullmatch(r"<[^>]+>", t) for t in tokens
    ):
        raise ValueError("unsupported_token_codec")
    pairs = [(c, i) for i, t in enumerate(tokens) for c in t]
    text = "".join(c for c, _ in pairs)
    kept = [
        p
        for n, p in enumerate(pairs)
        if not (
            p[0] == " "
            and n > 0
            and n + 1 < len(pairs)
            and ((_cjk(text[n - 1]) and _cjk(text[n + 1])) or _punct(text[n + 1]))
        )
    ]
    left, right = 0, len(kept)
    while left < right and kept[left][0].isspace():
        left += 1
    while right > left and kept[right - 1][0].isspace():
        right -= 1
    kept = kept[left:right]
    return "".join(c for c, _ in kept), [i for _, i in kept]


def inspect_snapshot(row, previous=None):
    """문자열과 원시 float 시각만 검증한다. 실패 시에도 원시를 보존하고 교체를 보류한다."""
    raw = row["native"]
    tokens = raw.get("tokens")
    ts = raw.get("timestamps")
    reasons = []
    origin = []
    rendered = None
    try:
        rendered, origin = render_tokens(tokens)
    except ValueError as e:
        reasons.append(str(e))
    if not isinstance(ts, list) or not isinstance(tokens, list) or len(ts) != len(tokens):
        reasons.append("token_time_length_mismatch")
    elif any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in ts):
        reasons.append("invalid_timestamp")
    elif ts != sorted(ts):
        reasons.append("nonmonotonic_token_time")
    else:
        seen = (row["end"] - row["source_start"]) / 16000 + (
            row["tail_padding_s"] if row["phase"] == "final" else 0
        )
        if any(t > seen for t in ts):
            reasons.append("timestamp_after_supplied_audio")
    if raw.get("segment") != 0 or raw.get("start_time") != 0:
        reasons.append("unsupported_time_origin")
    if (
        not isinstance(raw.get("text"), str)
        or raw["text"].strip() != row["text"]
        or rendered != row["text"]
    ):
        reasons.append("text_reconstruction_mismatch")
    prefix = True
    if previous is not None:
        before = previous["native"]
        prefix = (
            isinstance(tokens, list)
            and isinstance(ts, list)
            and isinstance(before.get("tokens"), list)
            and isinstance(before.get("timestamps"), list)
        )
        if prefix:
            n = len(before["tokens"])
            prefix = tokens[:n] == before["tokens"] and ts[:n] == before["timestamps"]
        if not prefix:
            reasons.append("prefix_revised")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "prefix_unchanged": prefix,
        "rendered": rendered,
        "origin": origin,
        "grid_frames": [round(t / 0.04) * 640 for t in ts] if not reasons else None,
    }


def map_job(snapshot, job, source, trustworthy=True, check=None):
    """토큰 point 범위의 구조 후보만 제안. token duration/음소 경계의 안전성은 미검증."""
    if snapshot is None:
        return {"state": "pending", "reasons": ["no_snapshot"]}
    check = inspect_snapshot(snapshot) if check is None else check
    reasons = list(check["reasons"])
    if not trustworthy:
        reasons.append("source_prefix_or_snapshot_invalid")
    if reasons:
        return {"state": "held", "reasons": reasons}
    raw = snapshot["native"]
    ts = raw["timestamps"]
    tokens = raw["tokens"]
    start = (job["start"] - source["start"]) / 16000
    end = (job["end"] - source["start"]) / 16000
    a, b = bisect_left(ts, start), bisect_left(ts, end)
    # 설치된 transducer의 10ms*4 시각 격자와 raw float의 절단이 다르면 보류한다.
    # 격자로 오디오 시간을 고치지 않는다. 양자화 경계의 모호성 검사만 한다.
    grid = check["grid_frames"]
    grid_cut = [
        bisect_left(grid, job["start"] - source["start"]),
        bisect_left(grid, job["end"] - source["start"]),
    ]
    if [a, b] != grid_cut:
        reasons.append("float_boundary_ambiguity")
    if a == b:
        reasons.append("empty_point_range")
    if a and a < len(tokens) and not tokens[a].startswith(" "):
        reasons.append("left_word_continuation")
    if b < len(tokens):
        if not tokens[b].startswith(" "):
            reasons.append("right_word_continuation")
        if ts[b] > (source["end"] - source["start"]) / 16000:
            reasons.append("right_context_in_padding")
    elif snapshot["phase"] != "final":
        reasons.append("need_right_context")
    # EOF는 그 파일의 실제 끝일 뿐 음향 정답의 단어 끝은 아니다.
    origin = check["origin"]
    ca, cb = bisect_left(origin, a), bisect_left(origin, b)
    return {
        "state": "held" if reasons else "structural_candidate",
        "reasons": reasons,
        "token_range": [a, b],
        "grid_comparison_token_range": grid_cut,
        "character_range": [ca, cb],
        "zip_span_text": snapshot["text"][ca:cb],
        "left_time_bracket": [ts[a - 1] if a else None, ts[a] if a < len(ts) else None],
        "right_time_bracket": [ts[b - 1] if b else None, ts[b] if b < len(ts) else None],
        "token_durations": None,
        "acoustic_alignment_validated": False,
    }


class AlignmentAudit:
    """단일 writer로 정답 없이 구조 후보와 보류 사유를 기록한다."""

    def __init__(self, sources):
        self.sources = {s["id"]: {k: s[k] for k in ("id", "start", "end")} for s in sources}
        self.latest = {}
        self.checks = {}
        self.trustworthy = {}
        self.jobs = {}
        self.received = {}
        self.last = {}

    def apply(self, event):
        if {"reference", "gold", "errors"} & event.keys():
            raise ValueError("정답을 진단 상태에 전달하지 않습니다.")
        kind = event["type"]
        if kind == "snapshot":
            sid = event["source_id"]
            row = event["snapshot"]
            prior = self.latest.get(sid)
            check = inspect_snapshot(row, prior)
            self.trustworthy[sid] = self.trustworthy.get(sid, True) and check["valid"]
            self.latest[sid] = row
            self.checks[sid] = check
        elif kind == "job":
            job = event["job"]
            i = job["index"]
            sid = job["source_id"]
            source = self.sources[sid]
            if i in self.jobs or not source["start"] <= job["start"] < job["end"] <= source["end"]:
                raise ValueError("작업 ID/소유 좌표 오류")
            self.jobs[i] = job
        elif kind == "receipt":
            result = event["result"]
            i = result["index"]
            if i not in self.jobs:
                raise ValueError("미등록 작업의 결과입니다.")
            job = self.jobs[i]
            sid = job["source_id"]
            if i in self.received or any(
                result[k] != job[k] for k in ("source_id", "start", "end", "payload_float32_sha256")
            ):
                raise ValueError("중복/다른 작업 결과 수신")
            self.received[i] = result
        else:
            raise ValueError("진단 이벤트 종류 오류")
        changed = []
        for i, job in self.jobs.items():
            if job["source_id"] != sid:
                continue
            snapshot = self.latest.get(sid)
            mapping = map_job(
                snapshot,
                job,
                self.sources[sid],
                self.trustworthy.get(sid, True),
                self.checks.get(sid),
            )
            if job["accepted"] is False:
                mapping = {"state": "held", "reasons": ["refine_dropped"]}
            elif i not in self.received:
                mapping = {"state": "pending", "reasons": ["awaiting_refine"]}
            elif self.received[i]["status"] != "success":
                mapping = {"state": "held", "reasons": ["refine_failed"]}
            # 변화만 기록하되 snapshot ID는 별도 provenance에 두어 같은 후보를 매 청크 반복하지 않는다.
            if mapping != self.last.get(i):
                self.last[i] = mapping
                changed.append(
                    {
                        "index": i,
                        "source_id": sid,
                        **mapping,
                        "snapshot_index": snapshot["index"] if snapshot else None,
                        "replacement_performed": False,
                    }
                )
        return changed


class TokenTraceReplay(OnlineVadReplay):
    """연속 Zip 청크/상태는 유지하고 native 스냅샷·수신·후보 진단만 추가한다."""

    def __init__(self, *args, token_log, audit_log, receipt_log, **kwargs):
        if kwargs.get("policy") != "vad0":
            raise ValueError("토큰 진단은 vad0만 지원합니다.")
        super().__init__(*args, **kwargs)
        self.token_log, self.audit_log, self.receipt_log = token_log, audit_log, receipt_log
        self.lock = RLock()
        self.audit = AlignmentAudit(self.sources)
        self.snapshots = 0
        self.observer = self.observe

    def _event(self, event):
        at = time.perf_counter_ns()
        changes = self.audit.apply(event)
        stored = (
            {
                "type": "snapshot",
                "source_id": event["source_id"],
                "snapshot_index": event["snapshot"]["index"],
            }
            if event["type"] == "snapshot"
            else event
        )
        write_event(
            self.audit_log,
            {
                "at_ns": at,
                "event": stored,
                "assessed_ns": time.perf_counter_ns(),
                "changes": changes,
            },
        )

    def observe(self, phase, stream, text, end, returned, source_index):
        began = time.perf_counter_ns()
        raw = self.adapter.token_snapshot(stream)
        source = self.sources[source_index]
        row = {
            "index": self.snapshots,
            "source_id": source["id"],
            "source_start": source["start"],
            "phase": phase,
            "end": end,
            "text": text,
            "returned_ns": returned,
            "native": raw,
            "tail_padding_s": self.adapter.model["tail_padding_s"],
            "snapshot_read_ns": time.perf_counter_ns(),
        }
        row["snapshot_read_wall_s"] = (row["snapshot_read_ns"] - began) / 1e9
        self.snapshots += 1
        with self.lock:
            write_event(self.token_log, row)
            self._event({"type": "snapshot", "source_id": source["id"], "snapshot": row})

    def offer_payload(self, payload, start, end, source, extra=None):
        with self.lock:
            original = self.submit

            def submit(job):
                kind, at = original(job)
                self._event(
                    {
                        "type": "job",
                        "job": {
                            k: job[k]
                            for k in (
                                "index",
                                "source_id",
                                "start",
                                "end",
                                "payload_float32_sha256",
                            )
                        }
                        | {"accepted": kind == "enqueue"},
                    }
                )
                return kind, at

            self.submit = submit
            try:
                super().offer_payload(payload, start, end, source, extra)
            finally:
                self.submit = original

    def receive(self, result, received):
        with self.lock:
            row = {
                k: result[k]
                for k in (
                    "index",
                    "source_id",
                    "start",
                    "end",
                    "payload_float32_sha256",
                    "status",
                    "ended_ns",
                )
            }
            row["received_ns"] = received
            self._event({"type": "receipt", "result": row})
            row["recorded_ns"] = time.perf_counter_ns()
            write_event(self.receipt_log, row)
