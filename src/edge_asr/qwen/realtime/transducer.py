"""공통 자막 이벤트로 상태 유지형 Transducer의 청크·endpoint를 기록한다."""

import time

from edge_asr.metrics import distribution


class TransducerSession:
    def __init__(self, adapter, emit, telemetry, *, frames=None):
        self.adapter, self.emit, self.telemetry = adapter, emit, telemetry
        self.frames = frames
        self.complete = False
        self.results, self.segments, self.skipped = [], [], []
        self.token_timings = {}
        self.position = self.segment_start = self.segment_id = 0
        self.text = ""
        self.chunks = []
        self.empty_endpoints = 0

    def metadata(self):
        return {
            "strategy": "stateful_transducer",
            "segmentation": "sherpa_onnx_endpoint_and_eof",
            "input": "original_pcm",
            "complete": self.complete,
            "frame_scope": "엔진에 전달한 원음 범위. 음향 토큰의 끝이나 모델 내부 lookahead 제외 시점이 아님.",
        }

    @property
    def scheduler_stats(self):
        return {
            "mode": "native_streaming",
            "decode_steps": len(self.chunks),
            "decode_wall_s": sum(self.chunks),
            "decode_step_s": distribution(self.chunks),
            "empty_endpoints": self.empty_endpoints,
        }

    def _decode(self, decode, stream):
        start = time.perf_counter()
        decode(stream)
        self.chunks.append(time.perf_counter() - start)

    def _publish(self, kind, text, begin, elapsed):
        identifier = len(self.results)
        row = {
            "type": "result",
            "kind": kind,
            "job_id": identifier,
            "revision": identifier,
            "segment_id": self.segment_id,
            "status": "success",
            "displayed": bool(text.strip()),
            "hypothesis": text,
            "start_frame": self.segment_start,
            "owned_start_frame": self.segment_start,
            "end_frame": self.position,
            "available_frame": self.position,
            "started_monotonic_s": begin,
            "published_s": time.perf_counter() - self.telemetry["t0_monotonic_ns"] / 1e9,
            "queue_wait_s": None,
            "inference_s": elapsed,
        }
        self.results.append(row)
        self.emit(row)

    def _commit(self, text, begin, elapsed, reason):
        if self.position <= self.segment_start:
            return
        if text.strip():
            self._publish("final", text, begin, elapsed)
            self.segments.append(
                {
                    "segment_id": self.segment_id,
                    "owned_start_frame": self.segment_start,
                    "owned_end_frame": self.position,
                    "reason": reason,
                }
            )
        else:
            self.empty_endpoints += 1
        self.segment_start = self.position
        self.segment_id += 1
        self.text = ""

    def start_stream(self):
        return self.adapter.start_stream()

    def accept_at(self, stream, samples, start, end):
        if start != self.position:
            raise RuntimeError(
                "입력 프레임이 누락되었습니다. Transducer 상태의 시간축이 불연속입니다."
            )
        self.position = end
        begin = time.perf_counter()
        text = self.adapter.accept_chunk(stream, samples, self._decode)
        elapsed = time.perf_counter() - begin
        if self.adapter.is_endpoint(stream):
            self._commit(text, begin, elapsed, "native_endpoint")
            self.adapter.reset_endpoint(stream)
        elif text != self.text:
            self._publish("partial", text, begin, elapsed)
            self.text = text
        return text

    def finish_stream(self, stream):
        if self.frames is not None and self.position != self.frames:
            raise RuntimeError("EOF까지 도착하지 못한 원음 프레임이 있습니다.")
        begin = time.perf_counter()
        text = self.adapter.finish_stream(stream, self._decode)
        self._commit(text, begin, time.perf_counter() - begin, "eof")
        self.complete = True
        return " ".join(row["hypothesis"] for row in self.results if row["kind"] == "final")
