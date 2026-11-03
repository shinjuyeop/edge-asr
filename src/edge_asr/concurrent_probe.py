"""측정 전용 WAV fan-out. 모델 결과를 병합하거나 서비스로 배포하지 않는다."""

import hashlib
import json
import multiprocessing
import os
import queue
import re
import sys
import time
from array import array
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread

from edge_asr.adapters import create_adapter
from edge_asr.dataset import Audio, load_audio, load_manifest
from edge_asr.diagnostics import thread_snapshot
from edge_asr.resources import RssSampler, process_sample
from edge_asr.runtime import environment

RATE, WINDOW = 16000, 80000


def write_event(handle, row):
    handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    handle.flush()


def float_digest(samples):
    pcm = array("f", samples)
    if sys.byteorder != "little":
        pcm.byteswap()
    return hashlib.sha256(pcm.tobytes()).hexdigest()


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


def limits_verified(text):
    return bool(re.search(r"Max address space\s+4294967296\s+4294967296\s+bytes", text))


def refine_worker(
    root,
    model,
    warmup,
    jobs,
    gate,
    stop,
    connection,
    output,
    factory=create_adapter,
    return_results=False,
):
    """한 번 로딩 후 FIFO 처리. 결과는 즉시 JSONL에 보존하고 병합하지 않는다."""
    adapter = None
    try:
        adapter = factory(model, RATE, root)
        began, cpu = time.perf_counter(), time.process_time()
        with RssSampler() as loading:
            adapter.load()
        info = {
            "status": "ready",
            "process_id": os.getpid(),
            "process_group_id": os.getpgrp(),
            "start_ticks": thread_snapshot(os.getpid())["process_start_ticks"],
            "loading_s": time.perf_counter() - began,
            "loading_cpu_s": time.process_time() - cpu,
            "loading_resources": loading.metadata(),
            "model_metadata": deepcopy(adapter.metadata),
        }
        began = time.perf_counter()
        for _ in range(3):
            if not isinstance(adapter.transcribe(warmup), str):
                raise TypeError("Refine 워밍업은 문자열을 반환해야 합니다.")
        info.update(
            warmup_runs=3,
            warmup_s=time.perf_counter() - began,
            limits_text=Path(f"/proc/{os.getpid()}/limits").read_text(),
            environment=environment(),
        )
        connection.send(info)
        count = 0
        with (output / "refine-results.jsonl").open("x", encoding="utf-8") as log:
            while not stop.is_set():
                with gate:
                    try:
                        job = jobs.get_nowait()
                    except queue.Empty:
                        job = "empty"
                    dequeued = time.perf_counter_ns()
                if job == "empty":
                    stop.wait(0.001)
                    continue
                if job is None:
                    connection.send(
                        {
                            "status": "completed",
                            "jobs": count,
                            "finished_ns": time.perf_counter_ns(),
                        }
                    )
                    stop.wait(5)
                    return
                if float_digest(job["samples"]) != job["payload_float32_sha256"]:
                    raise ValueError("Refine IPC PCM 해시 불일치")
                audio = Audio(job["samples"], RATE, job["payload_float32_sha256"])
                row = {k: v for k, v in job.items() if k != "samples"}
                row.update(dequeued_ns=dequeued, started_ns=time.perf_counter_ns(), status="failed")
                cpu = time.process_time()
                try:
                    text = adapter.transcribe(audio)
                    if not isinstance(text, str):
                        raise TypeError("Refine 전사는 문자열이어야 합니다.")
                    row.update(
                        status="success",
                        hypothesis=text,
                        adapter_result=deepcopy(getattr(adapter, "last_result_metadata", None)),
                    )
                except Exception as error:
                    row["error"] = f"{type(error).__name__}: {error}"
                    raise
                finally:
                    row.update(
                        ended_ns=time.perf_counter_ns(), process_cpu_s=time.process_time() - cpu
                    )
                    write_event(log, row)
                    if return_results:
                        connection.send({"status": "result", "result": row})
                count += 1
    except BaseException as error:
        try:
            connection.send({"status": "failed", "error": f"{type(error).__name__}: {error}"})
        except (BrokenPipeError, EOFError):
            pass
    finally:
        if adapter is not None:
            adapter.close()
        connection.close()
        jobs.cancel_join_thread()
        jobs.close()


class RefineChannel:
    """측정 하나의 Refine 자식/2구간 큐. 블로킹 입력 역압을 만들지 않는다."""

    def __init__(
        self, root, model, warmup, output, *, factory=create_adapter, return_results=False
    ):
        ctx = multiprocessing.get_context("spawn")
        self.jobs, self.gate, self.stop = ctx.Queue(maxsize=2), ctx.Lock(), ctx.Event()
        self.connection, sender = ctx.Pipe(duplex=False)
        self.process = ctx.Process(
            target=refine_worker,
            args=(
                root,
                model,
                warmup,
                self.jobs,
                self.gate,
                self.stop,
                sender,
                output,
                factory,
                return_results,
            ),
            daemon=True,
        )
        self.process.start()
        sender.close()
        self.ready = None

    def start(self):
        if not self.connection.poll(120):
            raise TimeoutError("Refine 모델 준비 제한 초과")
        self.ready = self.connection.recv()
        if self.ready["status"] != "ready":
            raise RuntimeError(str(self.ready))
        return self.ready

    def submit(self, job):
        if not self.process.is_alive():
            raise RuntimeError("Refine 프로세스 종료")
        with self.gate:
            try:
                self.jobs.put_nowait(job)
                return "enqueue", time.perf_counter_ns()
            except queue.Full:
                return "drop", time.perf_counter_ns()

    def finish(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            kind, _ = self.submit(None)
            if kind == "enqueue":
                break
            time.sleep(0.001)
        else:
            raise TimeoutError("Refine EOF 배출 제한 초과")
        if not self.connection.poll(max(0, deadline - time.monotonic())):
            raise TimeoutError("Refine 결과 배출 제한 초과")
        result = self.connection.recv()
        if result["status"] != "completed":
            raise RuntimeError(str(result))
        return result

    def close(self):
        self.stop.set()
        self.process.join(2)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(2)
        self.connection.close()
        self.jobs.cancel_join_thread()
        self.jobs.close()


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
        write_event(
            self.job_log,
            {k: v for k, v in job.items() if k != "samples"} | {"type": kind, "at_ns": at},
        )
        self.job_index += 1

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


class ProcessSampler:
    """모델 PID 합산 표본(100ms)과 실제 TID 표본(1초). 종료/결측을 0으로 채우지 않는다."""

    def __init__(self, roles, output):
        self.roles, self.output, self.stop = roles, output, Event()
        self.thread = Thread(target=self._run, daemon=True)
        self.samples, self.error = [], None

    def _run(self):
        try:
            with (
                (self.output / "process-resources.jsonl").open("x") as log,
                (self.output / "thread-resources.jsonl").open("x") as tids,
            ):
                last_threads = 0
                while True:
                    now = time.perf_counter_ns()
                    roles = self.roles()
                    row = {
                        "at_ns": now,
                        "roles": {
                            name: {"pid": pid, "sample": process_sample(pid)}
                            for name, pid in roles.items()
                            if pid
                        },
                    }
                    self.samples.append(row)
                    write_event(log, row)
                    if now - last_threads >= 1e9:
                        snapshots = {}
                        for name, pid in roles.items():
                            if name == "producer" or not pid:
                                continue
                            try:
                                snapshots[name] = {"pid": pid, **thread_snapshot(pid)}
                            except (OSError, ValueError, IndexError) as error:
                                snapshots[name] = {"pid": pid, "error": repr(error)}
                        write_event(
                            tids,
                            {
                                "at_ns": now,
                                "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                                "roles": snapshots,
                            },
                        )
                        last_threads = now
                    if self.stop.wait(0.1):
                        break
        except Exception as error:
            self.error = repr(error)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(2)
