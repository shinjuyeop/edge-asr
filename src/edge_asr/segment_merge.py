"""별도 실험 모드: 같은 VAD 소유 PCM의 구간별 Zip/Refine과 실제 parent 병합.

기존 연속 Zip 경로와 달리 구간마다 Zip 상태를 새로 만든다. UI는 실행하지 않는다.
"""

import time
import uuid
from array import array
from threading import RLock

from edge_asr.artifacts import write_event, write_json
from edge_asr.dataset import float_digest
from edge_asr.merging import POLICIES, Merger
from edge_asr.realtime.vad import OnlineVadReplay

RATE, CHUNK, HOLDBACK = 16000, 1600, 4800


class SegmentMergeReplay(OnlineVadReplay):
    """300ms 안전 보류 뒤 100ms씩 Zip에 공급하고 VAD 방출 시 같은 PCM을 마감한다.

    VAD 최대 종료 무음은 4800프레임이다. 활성 prefix를 이만큼 보류해 확정 끝을
    넘겨 입력하지 않는다. 짧은 구간은 방출 뒤에야 Zip을 시작할 수도 있다.
    """

    def __init__(self, *args, merge_log, segment_log, receipt_log, **kwargs):
        if kwargs.get("policy") != "vad0":
            raise ValueError("첫 구간 병합 실험은 문맥 없는 vad0만 지원합니다.")
        super().__init__(*args, **kwargs, progress=self.progress_segment)
        self.merge_log, self.segment_log, self.receipt_log = merge_log, segment_log, receipt_log
        self.lock = RLock()
        self.session_id = uuid.uuid4().hex
        self.engines = {p: Merger(self.session_id, p) for p in POLICIES}
        self.current = None
        self.segments = []
        self.receipts = []
        self.source_first = {}
        self.drained = False

    def start_stream(self):
        return self

    def _dispatch(self, event, **timing):
        # 호출자는 lock을 소유한다. 정답은 이 경로에 들어오지 않는다.
        for policy, engine in self.engines.items():
            at = time.perf_counter_ns()
            event = {**event, "session_id": engine.session_id, "at_ns": at}
            before = len(engine.updates)
            accepted = engine.apply(event)
            applied = time.perf_counter_ns()
            write_event(
                self.merge_log,
                {
                    "policy": policy,
                    "event": event,
                    "received_ns": timing.get("received_ns"),
                    "returned_ns": timing.get("returned_ns"),
                    "applied_ns": applied,
                    "acceptance": accepted,
                    "updates": engine.updates[before:],
                },
            )

    def _open(self, start):
        source = self.sources[self.source_index]
        absolute = source["start"] + start
        if self.current is not None:
            if self.current["start"] != absolute:
                raise RuntimeError("활성 Zip 구간과 VAD 시작 불일치")
            return
        key = f"{source['id']}::segment-{self.job_index:04d}"
        self.current = {
            "segment_key": key,
            "source_id": source["id"],
            "start": absolute,
            "cursor": start,
            "accepted": array("f"),
            "first_partial_ns": None,
        }
        with self.lock:
            for policy, engine in self.engines.items():
                at = time.perf_counter_ns()
                engine.register(
                    key, absolute, min(absolute + 80000, source["end"]), end_is_bound=True
                )
                write_event(
                    self.merge_log,
                    {
                        "policy": policy,
                        "event": {
                            "type": "register",
                            "source_id": key,
                            "session_id": self.session_id,
                            "start": absolute,
                            "end": min(absolute + 80000, source["end"]),
                            "at_ns": at,
                        },
                        "applied_ns": time.perf_counter_ns(),
                        "updates": [],
                    },
                )
        self.stream = self._call(self.adapter.start_stream)
        self.current["started_ns"] = time.perf_counter_ns()

    def _feed(self, pcm, start, phase):
        if not pcm:
            return
        source = self.sources[self.source_index]
        current = self.current
        if start != current["cursor"] or len(current["accepted"]) + len(pcm) > 80000:
            raise RuntimeError("Zip PCM 중복/누락 또는 상한 위반")
        samples = array("f", (x / 32768 for x in pcm))
        text = self._call(lambda: self.adapter.accept_chunk(self.stream, samples))
        returned = time.perf_counter_ns()
        if not isinstance(text, str):
            raise TypeError("Zip partial은 문자열이어야 합니다.")
        current["accepted"].extend(samples)
        current["cursor"] += len(pcm)
        end = source["start"] + current["cursor"]
        if text:
            if current["first_partial_ns"] is None:
                current["first_partial_ns"] = returned
            self.source_first.setdefault(source["id"], returned)
        with self.lock:
            self._dispatch(
                {"type": "partial", "source_id": current["segment_key"], "end": end, "text": text},
                returned_ns=returned,
            )
        write_event(
            self.zip_log,
            {
                "type": "partial",
                "source_index": self.source_index,
                "segment_key": current["segment_key"],
                "end": end,
                "at_ns": returned,
                "text": text,
                "input_start": source["start"] + start,
                "input_sha256": float_digest(samples),
                "delivered_through_frame": self.delivered_through,
                "feeding_phase": phase,
            },
        )

    def progress_segment(self, start, available_end, read):
        safe_end = available_end - HOLDBACK
        if safe_end <= start:
            return
        self._open(start)
        while safe_end - self.current["cursor"] >= CHUNK:
            cursor = self.current["cursor"]
            self._feed(read(cursor, cursor + CHUNK), cursor, "active_prefix")

    @staticmethod
    def _identity(job):
        return {
            "job_id": job["index"],
            "source_id": job["segment_key"],
            "start": job["start"],
            "end": job["end"],
            "context_start": job["context_start"],
            "context_end": job["context_end"],
            "payload_sha256": job["payload_float32_sha256"],
        }

    def offer_payload(self, payload, start, end, source, extra=None):
        if not extra or extra["context_start"] != start or extra["context_end"] != end:
            raise ValueError("같은 문맥 없는 소유 PCM이 필요합니다.")
        self._open(start - source["start"])
        current = self.current
        if current["cursor"] + source["start"] > end:
            raise RuntimeError("VAD 확정 끝을 넘어 Zip에 입력했습니다.")
        # 결과 thread가 job 등록보다 먼저 적용하지 못하도록 큐 제출까지 직렬화한다.
        with self.lock:
            original_submit = self.submit

            def submit(job):
                kind, at = original_submit(job)
                self._dispatch(
                    {"type": "job", **self._identity(job), "accepted": kind == "enqueue"}
                )
                return kind, at

            self.submit = submit
            try:
                super().offer_payload(
                    payload, start, end, source, {**extra, "segment_key": current["segment_key"]}
                )
            finally:
                self.submit = original_submit
        offset = current["cursor"] + source["start"] - start
        while offset < len(payload):
            block = payload[offset : offset + CHUNK]
            # payload는 float32이므로 PCM16으로 정확히 되돌린다. 원본은 PCM16 격자다.
            self._feed(
                array("h", (round(x * 32768) for x in block)), current["cursor"], "emission_catchup"
            )
            offset += len(block)
        if current["accepted"] != payload:
            raise ValueError("Zip/Refine에 실제 입력한 PCM이 다릅니다.")
        final = self._call(lambda: self.adapter.finish_stream(self.stream))
        returned = time.perf_counter_ns()
        if not isinstance(final, str):
            raise TypeError("Zip 최종은 문자열이어야 합니다.")
        row = {k: v for k, v in current.items() if k not in ("accepted", "cursor")}
        row.update(
            end=end,
            index=self.job_index - 1,
            hypothesis=final,
            finalized_ns=returned,
            emitted_ns=extra["emitted_ns"],
            payload_frames=len(payload),
            payload_float32_sha256=float_digest(current["accepted"]),
            first_partial_before_emission=current["first_partial_ns"] is not None
            and current["first_partial_ns"] < extra["emitted_ns"],
        )
        self.segments.append(row)
        write_event(self.segment_log, row)
        with self.lock:
            self._dispatch(
                {"type": "close", "source_id": current["segment_key"], "end": end, "text": final},
                returned_ns=returned,
            )
        self.current = self.stream = None

    def receive(self, result, received):
        with self.lock:
            event = {
                "type": "refine",
                **self._identity(result),
                "status": result["status"],
                "text": result.get("hypothesis"),
            }
            self._dispatch(event, received_ns=received, returned_ns=result["ended_ns"])
            row = {
                "index": result["index"],
                "segment_key": result["segment_key"],
                "ended_ns": result["ended_ns"],
                "received_ns": received,
                "recorded_ns": time.perf_counter_ns(),
            }
            self.receipts.append(row)
            write_event(self.receipt_log, row)

    def accept_at(self, stream, samples, start, end):
        if stream is not self or start != self.position or end - start != len(samples):
            raise RuntimeError("capture_gap: 입력 좌표 누락/중복")
        while self.position < end:
            source = self.sources[self.source_index]
            stop = min(end, source["end"])
            self.feed_refine(samples[self.position - start : stop - start], stop, source)
            self.position = stop
            if stop == source["end"]:
                if self.current is not None:
                    raise RuntimeError("원본 끝의 활성 Zip 구간이 남았습니다.")
                row = {
                    **source,
                    "status": "success",
                    "first_partial_ns": self.source_first.get(source["id"]),
                    "hypothesis": " ".join(
                        r["hypothesis"] for r in self.segments if r["source_id"] == source["id"]
                    ),
                    "finalized_ns": time.perf_counter_ns(),
                }
                self.source_rows.append(row)
                write_event(self.zip_log, {"type": "source_final", **row})
                self.source_index += 1
        with self.lock:
            return " ".join(s.text for s in self.engines["zip_only"].sources.values())

    def drain_finished(self):
        with self.lock:
            for policy, engine in self.engines.items():
                before = len(engine.updates)
                at = time.perf_counter_ns()
                engine.finish(at)
                write_event(
                    self.merge_log,
                    {
                        "policy": policy,
                        "event": {"type": "finish", "session_id": self.session_id, "at_ns": at},
                        "applied_ns": time.perf_counter_ns(),
                        "updates": engine.updates[before:],
                    },
                )
            self.drained = True

    def write_decisions(self, output):
        with self.lock:
            write_json(
                output / "merge-decisions.json",
                {
                    "drained": self.drained,
                    "policies": {
                        p: {
                            "finals": [s.decision for s in e.sources.values()],
                            "ignored_events": dict(e.ignored),
                        }
                        for p, e in self.engines.items()
                    },
                },
            )
