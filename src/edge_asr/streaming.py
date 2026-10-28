"""독립 입력 프로세스·유한 큐를 사용하는 1배속 WAV replay. 실제 마이크는 사용하지 않는다."""

from collections import deque
import json
import math
import multiprocessing
from pathlib import Path
import queue
import signal
import sys
import time

from edge_asr.benchmark import distribution


def _write(handle, event):
    handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
    handle.flush()


def _produce(frames, rate, chunk_frames, audio_queue, gate, stop, connection, log_path, drain_timeout):
    """PCM 자체는 부모에 있다. 청크의 프레임 구간만 정해진 시각에 공개한다."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    with Path(log_path).open("x", encoding="utf-8") as log:
        try:
            connection.send("ready")
            t0 = connection.recv()
            cpu_start = time.process_time()
            complete = True
            for index, start in enumerate(range(0, frames, chunk_frames)):
                end = min(start + chunk_frames, frames)
                due = end / rate
                while True:
                    remaining = due - (time.perf_counter_ns() - t0) / 1e9
                    if remaining <= 0 or stop.wait(remaining):
                        break
                if stop.is_set():
                    complete = False
                    break
                with gate:
                    at = (time.perf_counter_ns() - t0) / 1e9
                    event = {"type": "enqueue", "index": index, "start": start, "end": end,
                             "scheduled_s": due, "at_s": at}
                    try:
                        audio_queue.put_nowait((index, start, end, at, due))
                    except queue.Full:
                        event["type"] = "drop"
                _write(log, event)
            # EOF도 큐 용량을 기다리지만 실제 오디오 입력을 역압으로 늦추지 않는다.
            if complete:
                while not stop.is_set():
                    if (time.perf_counter_ns() - t0) / 1e9 > frames / rate + drain_timeout:
                        complete = False
                        break
                    with gate:
                        try:
                            audio_queue.put_nowait(None)
                            break
                        except queue.Full:
                            pass
                    stop.wait(0.001)
                else:
                    complete = False
            try:
                import resource
                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != "darwin" else 1024 ** 2)
            except ImportError:
                peak = None
            _write(log, {"type": "end", "complete": complete, "at_s": (time.perf_counter_ns() - t0) / 1e9,
                         "process_cpu_s": time.process_time() - cpu_start,
                         "lifetime_peak_rss_mib": peak})
            # 부모의 정상 반환/취소까지 기다린 뒤 feeder를 정리한다. 부모가 사라져도 유한 대기.
            stop.wait(drain_timeout + 2)
        except BaseException as error:
            _write(log, {"type": "error", "error": type(error).__name__, "message": str(error)})
            raise
        finally:
            connection.close()
            audio_queue.cancel_join_thread()
            audio_queue.close()


def read_events(path):
    if not path.exists():
        return []
    events = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except ValueError:
            if index != len(lines) - 1:
                raise
            # 강제 종료로 잘린 마지막 행은 완료 관측으로 취급하지 않는다.
    return events


def backlog_series(produced, consumed, rate, elapsed, audio_duration):
    """실제 enqueue/dequeue 이벤트로 100ms 격자와 이벤트 최대를 재구성한다."""
    events = [(e["at_s"], 0, e) for e in produced if e["type"] == "enqueue"]
    events += [(e["at_s"], 1, e) for e in consumed]
    events.sort(key=lambda item: (item[0], item[1]))
    pending, frames, position = deque(), 0, 0
    maximum, max_chunks, max_age = 0, 0, 0.0
    samples = []
    times = {i / 10 for i in range(math.floor(elapsed * 10) + 1)} | {elapsed, audio_duration}
    for at in sorted(t for t in times if t <= elapsed):
        while position < len(events) and events[position][0] <= at:
            event_at, kind, event = events[position]
            if pending:
                max_age = max(max_age, event_at - pending[0]["at_s"])
            if kind == 0:
                pending.append(event)
                frames += event["end"] - event["start"]
            else:
                if not pending or pending[0]["index"] != event["index"]:
                    raise ValueError("큐 이벤트의 순서·ID가 일치하지 않습니다.")
                first = pending.popleft()
                frames -= first["end"] - first["start"]
            maximum, max_chunks = max(maximum, frames), max(max_chunks, len(pending))
            position += 1
        age = at - pending[0]["at_s"] if pending else 0.0
        max_age = max(max_age, age)
        samples.append({"at_s": at, "queued_audio_s": frames / rate,
                        "queued_chunks": len(pending), "oldest_wait_s": age})
    return samples, {"sample_interval_s": 0.1, "sampling": "실제 큐 이벤트의 100ms 격자 재구성 및 오디오 끝/반환 경계",
                     "event_max_audio_s": maximum / rate, "event_max_chunks": max_chunks,
                     "event_max_oldest_wait_s": max_age,
                     "at_audio_end_s": next((s["queued_audio_s"] for s in samples if s["at_s"] == audio_duration), None),
                     "end_audio_s": frames / rate,
                     "audio_s_values": [s["queued_audio_s"] for s in samples],
                     "oldest_wait_s_values": [s["oldest_wait_s"] for s in samples]}


def warmup_stream(adapter, audio, settings):
    stream = adapter.start_stream()
    frames = audio.sample_rate_hz * settings["chunk_ms"] // 1000
    for start in range(0, len(audio.samples), frames):
        text = adapter.accept_chunk(stream, audio.samples[start:start + frames])
        if not isinstance(text, str):
            raise TypeError("어댑터가 문자열을 반환해야 합니다.")
    return adapter.finish_stream(stream)


def run_paced(adapter, audio, settings, directory: Path, telemetry: dict) -> str:
    directory.mkdir(parents=True)
    telemetry.update(complete=False, processing_s=0.0, processing_cpu_s=0.0,
                     first_partial_from_audio_start_s=None, finalization_from_audio_end_s=None)
    ctx = multiprocessing.get_context("spawn")
    audio_queue = ctx.Queue(maxsize=settings["queue_capacity_chunks"])
    gate, stop = ctx.Lock(), ctx.Event()
    connection, child_connection = ctx.Pipe()
    frames, rate = len(audio.samples), audio.sample_rate_hz
    chunk_frames = rate * settings["chunk_ms"] // 1000
    process = ctx.Process(target=_produce, args=(frames, rate, chunk_frames, audio_queue, gate, stop,
                                                child_connection, str(directory / "producer.jsonl"),
                                                settings["drain_timeout_s"]), daemon=True)
    consumed, durations, waits, capture_returns = [], [], [], []
    t0 = cpu_start = finish = finish_cpu = None
    final_text, first_partial = None, None
    returned_final = False

    def timed(call):
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            return call()
        finally:
            telemetry["processing_s"] += time.perf_counter() - wall
            telemetry["processing_cpu_s"] += time.process_time() - cpu

    try:
        process.start()
        child_connection.close()
        if not connection.poll(10) or connection.recv() != "ready":
            raise RuntimeError("입력 프로세스 준비 실패")
        stream = timed(adapter.start_stream)
        cpu_start, t0 = time.process_time(), time.perf_counter_ns()
        telemetry["t0_monotonic_ns"] = t0
        connection.send(t0)
        with (directory / "decoder.jsonl").open("x", encoding="utf-8") as log:
            while True:
                if (time.perf_counter_ns() - t0) / 1e9 > audio.duration_s + settings["drain_timeout_s"]:
                    raise TimeoutError("오디오 종료 후 큐 배출 제한을 초과했습니다.")
                with gate:
                    try:
                        item = audio_queue.get_nowait()
                    except queue.Empty:
                        item = "empty"
                    at = (time.perf_counter_ns() - t0) / 1e9
                if item == "empty":
                    if not process.is_alive():
                        raise RuntimeError("입력 프로세스가 EOF 없이 종료됐습니다.")
                    time.sleep(0.001)
                    continue
                if item is None:
                    break
                index, start, end, enqueued, due = item
                event = {"type": "dequeue", "index": index, "at_s": at}
                consumed.append(event)
                _write(log, event)
                begin = time.perf_counter_ns()
                text = timed(lambda: adapter.accept_chunk(stream, audio.samples[start:end]))
                returned = (time.perf_counter_ns() - t0) / 1e9
                if not isinstance(text, str):
                    raise TypeError("어댑터가 문자열을 반환해야 합니다.")
                if text and first_partial is None:
                    first_partial = returned
                duration = returned - (begin - t0) / 1e9
                durations.append(duration)
                waits.append(at - enqueued)
                capture_returns.append(returned - due)
                _write(log, {"type": "partial", "index": index, "at_s": returned,
                             "processing_s": duration, "text": text})
            final_text = timed(lambda: adapter.finish_stream(stream))
            finish = (time.perf_counter_ns() - t0) / 1e9
            finish_cpu = time.process_time()
            if not isinstance(final_text, str):
                raise TypeError("어댑터가 문자열을 반환해야 합니다.")
            _write(log, {"type": "final", "at_s": finish, "text": final_text})
            returned_final = True
    finally:
        if t0 is not None:
            telemetry["replay_elapsed_s"] = finish if finish is not None else (time.perf_counter_ns() - t0) / 1e9
            telemetry["replay_process_cpu_s"] = (finish_cpu if finish_cpu is not None else time.process_time()) - cpu_start
        # 정상 최종 반환 또는 오류·중단을 자식에게 알리고 feeder 교착을 피한다.
        stop.set()
        if process.pid is not None:
            process.join(2)
            if process.is_alive():
                stop.set()
                process.terminate()
                process.join(2)
        connection.close()
        child_connection.close()
        audio_queue.cancel_join_thread()
        audio_queue.close()
        produced = read_events(directory / "producer.jsonl")
        captures = [e for e in produced if e["type"] in {"enqueue", "drop"}]
        ends = [e for e in produced if e["type"] == "end"]
        producer_complete = bool(ends and ends[-1]["complete"] and process.exitcode == 0)
        dropped = [e for e in captures if e["type"] == "drop"]
        telemetry.update(producer_complete=producer_complete, producer_exit_code=process.exitcode,
                         producer_cpu_s=ends[-1]["process_cpu_s"] if ends else None,
                         producer_lifetime_peak_rss_mib=ends[-1]["lifetime_peak_rss_mib"] if ends else None,
                         planned_chunks=math.ceil(frames / chunk_frames), captured_chunks=len(captures),
                         accepted_chunks=sum(e["type"] == "enqueue" for e in captures), processed_chunks=len(durations),
                         dropped_chunks=len(dropped), dropped_audio_s=sum(e["end"] - e["start"] for e in dropped) / rate,
                         capture_lag_s_values=[max(0.0, e["at_s"] - e["scheduled_s"]) for e in captures],
                         chunk_processing_s_values=durations, queue_wait_s_values=waits,
                         capture_to_return_s_values=capture_returns,
                         first_partial_from_audio_start_s=first_partial,
                         no_partial_reason="no_nonempty_partial_before_flush" if first_partial is None else None)
        if t0 is not None:
            try:
                samples, backlog = backlog_series(produced, consumed, rate, telemetry["replay_elapsed_s"], audio.duration_s)
                with (directory / "backlog.jsonl").open("x", encoding="utf-8") as log:
                    for sample in samples:
                        _write(log, sample)
                telemetry["backlog"] = backlog
            except ValueError as error:
                telemetry["backlog_error"] = str(error)
        if returned_final and producer_complete and "backlog" in telemetry:
            telemetry["finalization_from_audio_end_s"] = finish - audio.duration_s
            telemetry["complete"] = (len(captures) == math.ceil(frames / chunk_frames)
                                     and len(durations) + len(dropped) == len(captures)
                                     and telemetry["backlog"]["end_audio_s"] == 0)
    if not telemetry["complete"]:
        raise RuntimeError("입력·큐·추론 이벤트가 완전하지 않습니다.")
    return final_text


def validate_telemetry(stats, row):
    if not isinstance(stats, dict) or stats.get("complete") is not True or stats.get("producer_complete") is not True:
        raise ValueError("성공한 paced_stream 결과에는 완료된 입력·추론 계측이 필요합니다.")

    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("스트리밍 계측은 유한한 0 이상 수여야 합니다.")

    for key in ("processing_s", "processing_cpu_s", "replay_elapsed_s", "replay_process_cpu_s",
                "finalization_from_audio_end_s", "dropped_audio_s"):
        number(stats.get(key))
    for key in ("planned_chunks", "captured_chunks", "accepted_chunks", "processed_chunks", "dropped_chunks"):
        if type(stats.get(key)) is not int or stats[key] < 0:
            raise ValueError("스트리밍 청크 수는 0 이상 정수여야 합니다.")
    if (stats["planned_chunks"] != stats["captured_chunks"] or
            stats["accepted_chunks"] + stats["dropped_chunks"] != stats["captured_chunks"] or
            stats["accepted_chunks"] != stats["processed_chunks"]):
        raise ValueError("스트리밍 입력·처리·드롭 청크 수가 일치하지 않습니다.")
    for key, count in (("chunk_processing_s_values", stats["processed_chunks"]),
                       ("queue_wait_s_values", stats["processed_chunks"]),
                       ("capture_to_return_s_values", stats["processed_chunks"]),
                       ("capture_lag_s_values", stats["captured_chunks"])):
        values = stats.get(key)
        if not isinstance(values, list) or len(values) != count:
            raise ValueError("스트리밍 표본 수가 일치하지 않습니다.")
        for value in values:
            number(value)
    first = stats.get("first_partial_from_audio_start_s")
    if first is not None:
        number(first)
        if first > stats["replay_elapsed_s"]:
            raise ValueError("첫 부분 출력이 최종 반환보다 늦습니다.")
    backlog = stats.get("backlog")
    if not isinstance(backlog, dict) or backlog.get("end_audio_s") != 0:
        raise ValueError("완료 결과의 큐가 비어 있지 않습니다.")
    for key in ("event_max_audio_s", "event_max_chunks", "event_max_oldest_wait_s", "at_audio_end_s"):
        number(backlog.get(key))
    for key in ("audio_s_values", "oldest_wait_s_values"):
        if not isinstance(backlog.get(key), list) or not backlog[key]:
            raise ValueError("백로그 표본이 필요합니다.")
        for value in backlog[key]:
            number(value)
    if len(backlog["audio_s_values"]) != len(backlog["oldest_wait_s_values"]):
        raise ValueError("백로그 표본 수가 다릅니다.")
    for key in ("producer_cpu_s", "producer_lifetime_peak_rss_mib"):
        if stats.get(key) is not None:
            number(stats[key])
    if (not math.isclose(stats["processing_s"], row["elapsed_s"]) or
            not math.isclose(stats["processing_cpu_s"], row["process_cpu_s"]) or
            stats["replay_elapsed_s"] < row["audio"]["duration_s"] or
            stats["dropped_audio_s"] > row["audio"]["duration_s"] or
            not math.isclose(stats["replay_elapsed_s"] - row["audio"]["duration_s"],
                             stats["finalization_from_audio_end_s"], abs_tol=1e-8) or
            stats.get("producer_exit_code") != 0):
        raise ValueError("공통 시간과 스트리밍 계측 시간이 일치하지 않습니다.")


def aggregate_streaming(items):
    valid = [row for row in items if row["status"] == "success"]
    stats = [row["streaming"] for row in valid]

    def values(key):
        return [value for s in stats for value in s[key]]

    def maximum(key):
        return max((s["backlog"][key] for s in stats), default=None)

    elapsed = sum(s["replay_elapsed_s"] for s in stats)
    duration = sum(row["duration_s"] for row in valid)
    return {
        "metric_scope": "successful_complete_capture; 실패·미실행은 지표 집계 제외, 완료율과 함께 해석",
        "included_files": len(stats),
        "no_partial_files": sum(s["first_partial_from_audio_start_s"] is None for s in stats),
        "first_partial_from_audio_start_s": distribution([s["first_partial_from_audio_start_s"] for s in stats
                                                         if s["first_partial_from_audio_start_s"] is not None]),
        "finalization_from_audio_end_s": distribution([s["finalization_from_audio_end_s"] for s in stats]),
        "chunk_processing_s": distribution(values("chunk_processing_s_values")),
        "queue_wait_s": distribution(values("queue_wait_s_values")),
        "latest_chunk_end_to_return_s": distribution(values("capture_to_return_s_values")),
        "capture_schedule_lag_s": distribution(values("capture_lag_s_values")),
        "dropped_chunks": sum(s["dropped_chunks"] for s in stats) if stats else None,
        "dropped_audio_s": sum(s["dropped_audio_s"] for s in stats) if stats else None,
        "captured_chunks": sum(s["captured_chunks"] for s in stats) if stats else None,
        "backlog_audio_s": distribution([v for s in stats for v in s["backlog"]["audio_s_values"]]),
        "backlog_oldest_wait_s": distribution([v for s in stats for v in s["backlog"]["oldest_wait_s_values"]]),
        "backlog_event_max_audio_s": maximum("event_max_audio_s"),
        "backlog_event_max_chunks": maximum("event_max_chunks"),
        "backlog_event_max_oldest_wait_s": maximum("event_max_oldest_wait_s"),
        "backlog_at_audio_end_s": distribution([s["backlog"]["at_audio_end_s"] for s in stats]),
        "backlog_end_max_audio_s": maximum("end_audio_s"),
        "total_replay_elapsed_s": elapsed if stats else None,
        "replay_elapsed_ratio": elapsed / duration if duration else None,
        "mean_replay_process_cpu_percent": sum(s["replay_process_cpu_s"] for s in stats) / elapsed * 100 if elapsed else None,
        "producer_cpu_s": sum(s["producer_cpu_s"] for s in stats) if stats and all(s["producer_cpu_s"] is not None for s in stats) else None,
        "producer_lifetime_peak_rss_mib": max((s["producer_lifetime_peak_rss_mib"] for s in stats
                                               if s["producer_lifetime_peak_rss_mib"] is not None), default=None),
    }
