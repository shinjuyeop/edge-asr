"""직전 부분 호출의 토큰을 같은 구간 확정 호출에만 일회성 후보로 전달한다."""

import ctypes
from copy import deepcopy


def validate_token_drafts(model):
    block = model.get("final_token_draft_block")
    if "final_token_draft_block" in model and (type(block) is not int or block not in (1, 2, 4, 8)):
        raise ValueError("final_token_draft_block은 1/2/4/8 정수여야 합니다. 1은 직렬 대조입니다.")
    return block


class FinalTokenDrafts:
    def __init__(self, model, build):
        self.block = validate_token_drafts(model)
        if self.block is None or "token_draft_v1" not in build.get("features", []):
            raise ValueError(
                "확정 토큰 후보에는 token_draft_v1 별도 빌드와 묶음 크기가 필요합니다."
            )
        self.limit = model["max_new_tokens"]
        self.setter = self.handle = None
        self.pending = self.active = self.previous = None
        self.expected = None

    def bind(self, library, handle):
        self.setter = library.edge_gguf_set_token_draft
        self.setter.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int32),
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self.setter.restype = ctypes.c_int
        self.handle = handle

    def prepare(self, job):
        self.pending = None
        fields = ("job_id", "revision", "segment_id", "start_frame", "end_frame", "available_frame")
        if (
            job.get("kind") not in ("partial", "final")
            or any(type(job.get(k)) is not int or job[k] < 0 for k in fields)
            or not job["start_frame"] < job["end_frame"] <= job["available_frame"]
        ):
            self.previous = None
            raise ValueError("토큰 후보에는 유효한 구간·revision·도착 음성 범위가 필요합니다.")
        self.pending = {k: deepcopy(job[k]) for k in (*fields, "kind")}

    def begin(self, frames):
        self.active, self.pending = self.pending, None
        job, previous = self.active, self.previous
        self.expected = None
        if job and frames != job["end_frame"] - job["start_frame"]:
            self.previous = None
            raise ValueError("토큰 후보 요청과 실제 PCM 길이가 다릅니다.")
        eligible = bool(
            job
            and previous
            and job["kind"] == "final"
            and job["segment_id"] == previous["segment_id"]
            and job["start_frame"] == previous["start_frame"]
            and job["job_id"] > previous["job_id"]
            and job["revision"] > previous["revision"]
            # VAD의 과거 종료점 보정으로 부분 PCM이 확정 PCM보다 길 수 있다.
            and previous["end_frame"] <= job["available_frame"]
        )
        tokens = previous["tokens"] if eligible else []
        block = self.block if job and job["kind"] == "final" else 1
        values = (ctypes.c_int32 * len(tokens))(*tokens)
        if self.setter is None or self.setter(self.handle, values, len(tokens), block):
            self.previous = None
            raise RuntimeError("확정 토큰 후보 설정 실패")
        self.expected = {"candidate_tokens": len(tokens), "block": block}
        self.previous = None  # 모든 호출에서 소비. 성공/협력적 취소 부분 호출만 다시 저장한다.
        return {
            "policy": "previous_partial_same_segment_v1",
            **self.expected,
            "source": {k: v for k, v in previous.items() if k != "tokens"} if eligible else None,
        }

    def verify(self, result):
        actual = result.get("token_draft", {})
        if self.expected is None or any(actual.get(k) != v for k, v in self.expected.items()):
            raise ValueError("확정 토큰 후보의 native 적용 범위가 다릅니다.")

    def finish(self, metadata, status):
        job, self.active = self.active, None
        self.pending = self.expected = self.previous = None
        if not job or job["kind"] != "partial" or not metadata:
            return
        native = metadata.get("native_result", {})
        tokens = native.get("token_ids")
        valid = (
            native.get("schema_version") == 1
            and native.get("input_samples") == job["end_frame"] - job["start_frame"]
            and isinstance(tokens, list)
            and 0 < len(tokens) <= self.limit
            and all(type(t) is int and 0 <= t < 2**31 for t in tokens)
        )
        success = status == "success" and native.get("status") == "success"
        cancelled = (
            status == "cancelled"
            and metadata.get("cancellation") == "final_pending"
            and native.get("status") == "failed"
            and native.get("termination") == "callback_error"
        )
        if valid and (success or cancelled):
            self.previous = {**job, "status": status, "tokens": list(tokens)}

    def close(self):
        self.setter = self.handle = None
        self.pending = self.active = self.previous = self.expected = None
