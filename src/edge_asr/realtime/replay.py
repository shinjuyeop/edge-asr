"""원본 좌표를 유지하는 WAV fan-out과 연속 Zip replay. 모델 결과를 병합하지 않는다."""

import time
from array import array

from edge_asr.artifacts import write_event
from edge_asr.dataset import Audio, float_digest, load_audio, load_manifest

RATE, WINDOW = 16000, 80000


def prepare_track(root, manifest):
    sources, frames = [], array("f")
    samples, manifest_sha = load_manifest(manifest, root)
    for sample in samples:
        audio = load_audio(sample.audio_path, RATE)
        if sample.audio_sha256 is None or audio.sha256 != sample.audio_sha256:
            raise ValueError("모든 원본 WAV 해시가 일치해야 합니다.")
        start = len(frames)
        frames.extend(audio.samples)
        sources.append(
            {
                "id": sample.id,
                "reference": sample.reference,
                "audio_sha256": audio.sha256,
                "start": start,
                "end": len(frames),
            }
        )
    return Audio(frames, RATE, float_digest(frames)), sources, manifest_sha


class ConcurrentReplay:
    """공통 100ms 청크를 원본 발화/5초 경계에서 나눠 Zipformer와 Refine에 전달한다."""

    def __init__(self, adapter, sources, telemetry, submit, zip_log, job_log):
        self.adapter, self.sources, self.telemetry, self.submit = (
            adapter,
            sources,
            telemetry,
            submit,
        )
        self.zip_log, self.job_log = zip_log, job_log
        self.position = self.source_index = self.job_index = 0
        self.buffer = array("f")
        self.source_rows, self.zip_calls = [], []
        self.stream = None
        self.job_start = 0
        self.next_job_end = min(sources[0]["end"], WINDOW)
        self.observer = None

    def _call(self, call):
        began, cpu = time.perf_counter_ns(), time.process_time()
        try:
            return call()
        finally:
            row = {
                "type": "zip_call",
                "source_index": self.source_index,
                "started_ns": began,
                "ended_ns": time.perf_counter_ns(),
                "process_cpu_s": time.process_time() - cpu,
            }
            self.zip_calls.append(row)
            write_event(self.zip_log, row)

    def start_stream(self):
        self.stream = self._call(self.adapter.start_stream)
        return self

    def offer_payload(self, payload, start, end, source, extra=None):
        job = {
            "index": self.job_index,
            "source_index": self.source_index,
            "source_id": source["id"],
            "start": start,
            "end": end,
            "payload_frames": len(payload),
            "payload_float32_sha256": float_digest(payload),
            "submitted_ns": time.perf_counter_ns(),
            "samples": payload,
            **(extra or {}),
        }
        kind, at = self.submit(job) if self.submit else ("control_only", time.perf_counter_ns())
        row = {k: v for k, v in job.items() if k != "samples"} | {"type": kind, "at_ns": at}
        write_event(self.job_log, row)
        self.job_index += 1
        return row

    def feed_refine(self, block, stop, source):
        self.buffer.extend(block)
        if stop == self.next_job_end:
            payload = self.buffer
            self.buffer = array("f")
            self.offer_payload(payload, self.job_start, stop, source)
            self.job_start = stop

    def accept_at(self, stream, samples, start, end):
        if stream is not self or start != self.position or end - start != len(samples):
            raise RuntimeError("capture_gap: 손실 입력을 압축해 원본 좌표로 사용하지 않습니다.")
        text = ""
        while self.position < end:
            source = self.sources[self.source_index]
            if len(self.source_rows) == self.source_index:
                self.source_rows.append(
                    {**source, "status": "not_attempted", "first_partial_ns": None}
                )
            row = self.source_rows[self.source_index]
            stop = min(end, source["end"], self.next_job_end)
            block = samples[self.position - start : stop - start]
            self.feed_refine(block, stop, source)
            if stop == self.next_job_end:
                self.next_job_end = min(stop + WINDOW, source["end"])
            text = self._call(lambda: self.adapter.accept_chunk(self.stream, block))
            if not isinstance(text, str):
                raise TypeError("Streaming 전사는 문자열이어야 합니다.")
            returned = time.perf_counter_ns()
            if text and row["first_partial_ns"] is None:
                row["first_partial_ns"] = returned
            if self.observer is not None:
                self.observer("partial", self.stream, text, stop, returned, self.source_index)
            write_event(
                self.zip_log,
                {
                    "type": "partial",
                    "source_index": self.source_index,
                    "end": stop,
                    "at_ns": returned,
                    "text": text,
                },
            )
            self.position = stop
            if stop == source["end"]:
                final = self._call(lambda: self.adapter.finish_stream(self.stream))
                if not isinstance(final, str):
                    raise TypeError("Streaming 최종 전사는 문자열이어야 합니다.")
                row.update(status="success", hypothesis=final, finalized_ns=time.perf_counter_ns())
                if self.observer is not None:
                    self.observer(
                        "final", self.stream, final, stop, row["finalized_ns"], self.source_index
                    )
                write_event(self.zip_log, {"type": "source_final", **row})
                self.source_index += 1
                if self.source_index < len(self.sources):
                    self.stream = self._call(self.adapter.start_stream)
                    self.next_job_end = min(stop + WINDOW, self.sources[self.source_index]["end"])
        return text

    def finish_stream(self, stream):
        if self.position != self.sources[-1]["end"] or self.source_index != len(self.sources):
            raise RuntimeError("입력/발화가 완료되지 않았습니다.")
        return " ".join(r["hypothesis"] for r in self.source_rows)
