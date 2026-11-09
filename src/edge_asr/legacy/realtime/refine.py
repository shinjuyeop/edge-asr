"""유한 큐를 가진 Offline Refine 프로세스와 선택적 비동기 결과 수신."""

import multiprocessing
import os
import queue
import time
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread

from edge_asr.adapters import create_adapter
from edge_asr.artifacts import write_event
from edge_asr.dataset import Audio, float_digest
from edge_asr.diagnostics import thread_snapshot
from edge_asr.resources import RssSampler
from edge_asr.runtime import environment

RATE = 16000


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
                    row["adapter_result"] = deepcopy(getattr(adapter, "last_result_metadata", None))
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
        self.return_results = return_results
        self.done = Event()
        self.reader = None
        self.completion = self.error = None

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

    def listen(self, callback):
        if not self.return_results:
            raise RuntimeError("return_results=True인 채널만 결과 reader를 연결할 수 있습니다.")
        if self.reader is not None:
            raise RuntimeError("이미 결과 reader가 있습니다.")

        def receive():
            try:
                while not self.stop.is_set():
                    if not self.connection.poll(0.1):
                        if not self.process.is_alive():
                            raise RuntimeError("Refine 반환 전 프로세스 종료")
                        continue
                    message = self.connection.recv()
                    received = time.perf_counter_ns()
                    if message["status"] == "result":
                        callback(message["result"], received)
                    elif message["status"] == "completed":
                        self.completion = message
                        return
                    else:
                        raise RuntimeError(str(message))
            except BaseException as error:
                self.error = repr(error)
            finally:
                self.done.set()

        self.reader = Thread(target=receive, daemon=True)
        self.reader.start()

    def finish(self, timeout=30):
        if self.return_results and self.reader is None:
            raise RuntimeError("결과 반환 모드는 listen(callback)을 먼저 연결해야 합니다.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.reader is not None and self.done.is_set():
                raise RuntimeError(self.error or "Refine이 EOF 전에 종료됐습니다.")
            kind, _ = self.submit(None)
            if kind == "enqueue":
                break
            time.sleep(0.001)
        else:
            raise TimeoutError("Refine EOF 배출 제한 초과")
        if self.reader is not None:
            if not self.done.wait(max(0, deadline - time.monotonic())):
                raise TimeoutError("Refine 반환 제한 초과")
            if self.error or self.completion is None:
                raise RuntimeError(self.error or "Refine 완료 누락")
            return self.completion
        if not self.connection.poll(max(0, deadline - time.monotonic())):
            raise TimeoutError("Refine 결과 배출 제한 초과")
        result = self.connection.recv()
        if result["status"] != "completed":
            raise RuntimeError(str(result))
        return result

    def close(self):
        self.stop.set()
        if self.reader is not None:
            self.reader.join(2)
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
        if self.reader is not None and self.reader.is_alive():
            raise RuntimeError("Refine reader 회수 실패")
