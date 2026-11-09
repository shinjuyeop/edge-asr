"""저장 로그 재생용 토큰 ID 기반 교체. 입력 모델/정답/UI에 접근하지 않는다."""

import copy
import hashlib
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field

from edge_asr.legacy.token_alignment import inspect_snapshot, map_job

POLICIES = ("zip_only", "guarded_replace_v1")


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Caption:
    start: int
    end: int
    snapshot: dict | None = None
    check: dict | None = None
    trusted: bool = True
    text: str = ""
    patches: dict = field(default_factory=dict)
    revision: int = 0
    proof: dict | None = None
    shown_patch_ids: tuple = ()


class TokenSplice:
    """원본 스냅샷에서만 조립해 길이가 다른 교체와 이후 append를 분리한다."""

    def __init__(self, session_id, sources, policy):
        if not isinstance(session_id, str) or not session_id or policy not in POLICIES:
            raise ValueError("세션 ID와 지원 정책이 필요합니다.")
        self.session_id, self.policy = session_id, policy
        self.sources = {}
        previous_end = 0
        for source in sources:
            sid, start, end = (source[k] for k in ("id", "start", "end"))
            if (
                not isinstance(sid, str)
                or not sid
                or sid in self.sources
                or type(start) is not int
                or type(end) is not int
                or not previous_end <= start < end
            ):
                raise ValueError("고유 원본 ID와 비중첩 오디오 좌표가 필요합니다.")
            self.sources[sid] = Caption(start, end)
            previous_end = end
        self.jobs = {}
        self.receipts = {}
        self.blocked = {}
        self.updates = []
        self.decisions = []
        self.ignored = Counter()
        self.last_ns = -1
        self.finished = False
        self.conservation_checks = 0

    def _decision(self, job_id, at, reason, accepted=False):
        row = {"job_id": job_id, "at_ns": at, "reason": reason, "accepted": accepted}
        self.decisions.append(row)
        return row

    def _drop_patch(self, job_id, at, reason):
        job = self.jobs[job_id]
        source = self.sources[job["source_id"]]
        source.patches.pop(job_id, None)
        self.blocked[job_id] = reason
        return self._decision(job_id, at, reason)

    @staticmethod
    def _same_job(result, job):
        return all(
            result.get(k) == job[k]
            for k in ("index", "source_id", "start", "end", "payload_float32_sha256")
        )

    @staticmethod
    def _range(source, patch):
        row = source.snapshot
        a, b = patch["token_range"]
        raw = row["native"]
        if raw["tokens"][a:b] != patch["tokens"] or raw["timestamps"][a:b] != patch["timestamps"]:
            return None
        origins = source.check["origin"]
        ca, cb = bisect_left(origins, a), bisect_left(origins, b)
        if [ca, cb] != patch["character_range"] or row["text"][ca:cb] != patch["zip_span_text"]:
            return None
        return ca, cb

    def _show(self, sid, at, reason):
        source = self.sources[sid]
        original = source.snapshot["text"] if source.snapshot else ""
        pieces = []
        parts = []
        cursor = 0
        out = 0
        for job_id, patch in sorted(
            source.patches.items(), key=lambda item: item[1]["character_range"]
        ):
            a, b = patch["character_range"]
            if not cursor <= a < b <= len(original):
                raise ValueError("교체 범위가 중복되거나 원본 밖입니다.")
            if cursor < a:
                text = original[cursor:a]
                pieces.append(text)
                parts.append(
                    {
                        "kind": "zip",
                        "original_range": [cursor, a],
                        "output_range": [out, out + len(text)],
                        "text_sha256": text_digest(text),
                    }
                )
                out += len(text)
            text = patch["replacement"]
            pieces.append(text)
            parts.append(
                {
                    "kind": "refine",
                    "job_id": job_id,
                    "original_range": [a, b],
                    "output_range": [out, out + len(text)],
                    "text_sha256": text_digest(text),
                }
            )
            out += len(text)
            cursor = b
        if cursor < len(original):
            text = original[cursor:]
            pieces.append(text)
            parts.append(
                {
                    "kind": "zip",
                    "original_range": [cursor, len(original)],
                    "output_range": [out, out + len(text)],
                    "text_sha256": text_digest(text),
                }
            )
        visible = "".join(pieces)
        kept = "".join(
            original[p["original_range"][0] : p["original_range"][1]]
            for p in parts
            if p["kind"] == "zip"
        )
        actual = "".join(
            visible[p["output_range"][0] : p["output_range"][1]]
            for p in parts
            if p["kind"] == "zip"
        )
        if kept != actual:
            raise ValueError("비선택 Zip 문자가 소실/변경됐습니다.")
        removed = sum(
            b - a for a, b in (p["original_range"] for p in parts if p["kind"] == "refine")
        )
        if len(kept) + removed != len(original):
            raise ValueError("원본 문자 소유 합이 맞지 않습니다.")
        self.conservation_checks += 1
        proof = {
            "original_characters": len(original),
            "retained_zip_characters": len(kept),
            "replaced_original_characters": removed,
            "retained_original_sha256": text_digest(kept),
            "retained_output_sha256": text_digest(actual),
            "mechanical_missing_or_changed_zip_characters": 0,
            "parts": parts,
        }
        patch_ids = tuple(sorted(source.patches))
        if visible != source.text or patch_ids != source.shown_patch_ids:
            source.revision += 1
            self.updates.append(
                {
                    "session_id": self.session_id,
                    "source_id": sid,
                    "at_ns": at,
                    "reason": reason,
                    "snapshot_index": source.snapshot["index"] if source.snapshot else None,
                    "revision": source.revision,
                    "text": visible,
                    "previous_text": source.text,
                    "text_changed": visible != source.text,
                    "active_patch_ids": list(patch_ids),
                    "original_zip_text": original,
                    "proof": proof,
                }
            )
        source.text = visible
        source.proof = proof
        source.shown_patch_ids = patch_ids

    def _candidate(self, change, at):
        i = change["index"]
        if i not in self.jobs:
            self.ignored["unknown_job"] += 1
            return
        job = self.jobs[i]
        sid = job["source_id"]
        source = self.sources[sid]
        if change["source_id"] != sid:
            self.ignored["job_source_mismatch"] += 1
            return
        if self.policy == "zip_only":
            self._decision(i, at, "zip_only")
            return
        if change["state"] != "structural_candidate":
            if i in source.patches:
                self._drop_patch(i, at, "candidate_revoked")
                self._show(sid, at, "candidate_revoked")
            else:
                self._decision(i, at, "diagnostic_not_candidate")
            return
        if i in self.blocked:
            self._decision(i, at, "job_blocked")
            return
        receipt = self.receipts.get(i)
        reason = None
        if not source.trusted:
            reason = "source_invalid"
        elif not job["accepted"]:
            reason = "refine_dropped"
        elif receipt is None:
            reason = "missing_refine"
        elif receipt["status"] != "success":
            reason = "refine_failed"
        elif not receipt["text"].strip():
            reason = "empty_refine"
        elif source.snapshot is None or change["snapshot_index"] != source.snapshot["index"]:
            reason = "snapshot_mismatch"
        if reason:
            self._drop_patch(i, at, reason)
            self._show(sid, at, reason)
            return
        expected = map_job(
            source.snapshot,
            job,
            {"start": source.start, "end": source.end},
            source.trusted,
            source.check,
        )
        if expected["state"] != "structural_candidate" or any(
            expected.get(k) != change.get(k)
            for k in ("token_range", "character_range", "zip_span_text")
        ):
            self._drop_patch(i, at, "structural_gate_mismatch")
            self._show(sid, at, "structural_gate_mismatch")
            return
        a, b = change["token_range"]
        ca, cb = change["character_range"]
        raw = source.snapshot["native"]
        if (
            any(type(x) is not int for x in (a, b, ca, cb))
            or not 0 <= a < b <= len(raw["tokens"])
            or not 0 <= ca < cb <= len(source.snapshot["text"])
        ):
            self._drop_patch(i, at, "invalid_range")
            self._show(sid, at, "invalid_range")
            return
        patch = {
            "token_range": [a, b],
            "character_range": [ca, cb],
            "zip_span_text": change["zip_span_text"],
            "tokens": raw["tokens"][a:b],
            "timestamps": raw["timestamps"][a:b],
            "replacement": receipt["text"],
            "candidate_at_ns": at,
            "payload_float32_sha256": job["payload_float32_sha256"],
        }
        if self._range(source, patch) is None:
            self._drop_patch(i, at, "anchor_mismatch")
            self._show(sid, at, "anchor_mismatch")
            return
        if i in source.patches:
            old = source.patches[i]
            if all(
                old[k] == patch[k]
                for k in ("token_range", "character_range", "zip_span_text", "replacement")
            ):
                self.ignored["duplicate_candidate"] += 1
                return
            self._drop_patch(i, at, "conflicting_candidate")
            self._show(sid, at, "conflicting_candidate")
            return
        if any(
            max(ca, p["character_range"][0]) < min(cb, p["character_range"][1])
            or max(a, p["token_range"][0]) < min(b, p["token_range"][1])
            for p in source.patches.values()
        ):
            self._drop_patch(i, at, "overlapping_range")
            return
        source.patches[i] = patch
        self._decision(i, at, "applied_in_replay", True)
        self._show(sid, at, "applied_in_replay")

    def apply(self, event):
        if {"reference", "gold", "errors"} & event.keys():
            raise ValueError("정답을 교체기에 전달하지 않습니다.")
        if event.get("session_id") != self.session_id:
            self.ignored["wrong_session"] += 1
            return
        if self.finished:
            self.ignored["after_finish"] += 1
            return
        at = event["at_ns"]
        if type(at) is not int or at < 0 or at < self.last_ns:
            raise ValueError("이벤트 단조 시계 순서 오류")
        self.last_ns = at
        kind = event["type"]
        if kind == "snapshot":
            row = copy.deepcopy(event["snapshot"])
            sid = row["source_id"]
            if sid not in self.sources:
                self.ignored["unknown_source"] += 1
                return
            source = self.sources[sid]
            if (
                row["source_start"] != source.start
                or not source.start < row["end"] <= source.end
                or not isinstance(row["text"], str)
                or row["phase"] not in ("partial", "final")
                or (row["phase"] == "final" and row["end"] != source.end)
            ):
                raise ValueError("원본/스냅샷 좌표 오류")
            if source.snapshot is not None and (
                source.snapshot["phase"] == "final" or row["index"] <= source.snapshot["index"]
            ):
                self.ignored["stale_snapshot"] += 1
                return
            if source.snapshot is not None and row["end"] < source.snapshot["end"]:
                raise ValueError("입력 프레임 시계가 역행했습니다.")
            check = inspect_snapshot(row, source.snapshot)
            source.trusted = source.trusted and check["valid"]
            source.snapshot = row
            source.check = check
            for i, p in list(source.patches.items()):
                if not source.trusted:
                    self._drop_patch(i, at, "source_invalid")
                elif self._range(source, p) is None:
                    self._drop_patch(i, at, "anchor_drift")
            self._show(sid, at, "zip_snapshot")
        elif kind == "job":
            job = copy.deepcopy(event["job"])
            i = job["index"]
            sid = job["source_id"]
            if i in self.jobs or sid not in self.sources:
                raise ValueError("작업 중복/알 수 없는 원본")
            source = self.sources[sid]
            if (
                type(i) is not int
                or i < 0
                or type(job["accepted"]) is not bool
                or not source.start <= job["start"] < job["end"] <= source.end
            ):
                raise ValueError("작업 ID/좌표/승인 값 오류")
            sha = job["payload_float32_sha256"]
            if (
                not isinstance(sha, str)
                or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise ValueError("작업 PCM 해시 오류")
            self.jobs[i] = job
        elif kind == "receipt":
            receipt = copy.deepcopy(event["result"])
            i = receipt["index"]
            if i not in self.jobs:
                self.ignored["unknown_job"] += 1
                return
            if not self._same_job(receipt, self.jobs[i]):
                raise ValueError("다른 작업/PCM의 Refine 반환")
            if i in self.receipts:
                self.ignored["duplicate_receipt"] += 1
                return
            if receipt["status"] not in ("success", "failed") or (
                receipt["status"] == "success" and not isinstance(receipt.get("text"), str)
            ):
                raise ValueError("Refine 상태/문자열 오류")
            self.receipts[i] = receipt
        elif kind == "candidate":
            self._candidate(event["change"], at)
        else:
            raise ValueError("지원하지 않는 교체 이벤트")

    def finish(self, at_ns):
        if type(at_ns) is not int or at_ns < self.last_ns:
            raise ValueError("종료 시계 오류")
        if any(s.snapshot is None or s.snapshot["phase"] != "final" for s in self.sources.values()):
            raise ValueError("Zip 파일 종료가 미완료입니다.")
        for sid, source in self.sources.items():
            self._show(sid, at_ns, "drain_finished")
        self.finished = True
        return [
            {
                "source_id": sid,
                "text": s.text,
                "original_zip_text": s.snapshot["text"],
                "active_patch_ids": list(sorted(s.patches)),
                "proof": s.proof,
            }
            for sid, s in self.sources.items()
        ]
