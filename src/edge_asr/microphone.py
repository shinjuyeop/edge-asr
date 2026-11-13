"""ALSA 마이크 수집·유한 큐·원음 보존. 모델 런타임에 의존하지 않는다."""

import math
import os
import queue
import re
import selectors
import subprocess
import threading
import time
import wave
from array import array

from edge_asr.artifacts import write_event
from edge_asr.dataset import load_audio


def devices():
    result = subprocess.run(
        ["arecord", "-l"],
        capture_output=True,
        text=True,
        timeout=5,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "녹음 장치 조회 실패")
    return [
        {"id": f"plughw:CARD={card},DEV={device}", "name": f"{name} · {label}"}
        for card, name, device, label in re.findall(
            r"^card \d+: (\S+) \[([^\]]+)\], device (\d+): ([^\[]+)\[", result.stdout, re.M
        )
    ]


def record(
    device,
    seconds,
    output,
    telemetry,
    accept,
    *,
    start_gate=None,
    on_start=None,
    on_update=None,
    stop_signal=None,
    command=None,
):
    """16kHz PCM16을100ms로 수집한다. 넘침·장치 오류를 숨기지 않고 실패한다.

    command는 실제 ALSA 대신 제한된 입력 프로세스를 쓰는 Host 검사 전용 주입점이다.
    """
    if not math.isfinite(seconds) or not 0 < seconds <= 600:
        raise ValueError("마이크 입력 제한은0초 초과600초 이하입니다.")
    target, limit = output / "recording.wav", round(seconds * 16000)
    if target.exists():
        raise FileExistsError(target)
    directory = output / "stream"
    directory.mkdir()
    messages, stop = queue.Queue(maxsize=10), threading.Event()
    stats = {
        "captured_frames": 0,
        "captured_chunks": 0,
        "processed_chunks": 0,
        "dropped_chunks": 0,
        "hardware_overruns": 0,
        "rms_dbfs": None,
        "peak": 0.0,
        "clipped_samples": 0,
        "error": None,
        "stop_reason": None,
    }
    command = command or [
        "arecord",
        f"--device={device}",
        "--file-type=raw",
        "--format=S16_LE",
        "--channels=1",
        "--rate=16000",
        "--buffer-time=200000",
        "--period-time=100000",
    ]
    telemetry["capture_command"] = command
    if start_gate:
        start_gate()
    origin = time.perf_counter_ns()
    telemetry.update(t0_monotonic_ns=origin, complete=False, dropped_chunks=0)
    if on_start:
        on_start(origin)

    def produce():
        process = None
        try:
            with (output / "capture.log").open("xb") as errors, target.open("xb") as raw:
                with wave.open(raw, "wb") as wav, (directory / "producer.jsonl").open("x") as log:
                    wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=errors,
                        env={**os.environ, "LC_ALL": "C"},
                    )
                    telemetry["producer_pid"] = process.pid
                    pending = bytearray()

                    def publish(data):
                        pcm = array("h")
                        pcm.frombytes(data)
                        if os.sys.byteorder != "little":
                            pcm.byteswap()
                        begin = stats["captured_frames"]
                        stats["captured_frames"] += len(pcm)
                        stats["captured_chunks"] += 1
                        peak = max((abs(x) for x in pcm), default=0) / 32768
                        rms = math.sqrt(sum(x * x for x in pcm) / len(pcm)) / 32768
                        stats.update(peak=peak, rms_dbfs=20 * math.log10(rms) if rms else None)
                        stats["clipped_samples"] += sum(abs(x) >= 32767 for x in pcm)
                        wav.writeframes(data)
                        row = {
                            "start": begin,
                            "end": stats["captured_frames"],
                            "at_s": (time.perf_counter_ns() - origin) / 1e9,
                            "type": "enqueue",
                        }
                        try:
                            messages.put_nowait((begin, array("f", (x / 32768 for x in pcm))))
                        except queue.Full:
                            row["type"] = "drop"
                            stats["dropped_chunks"] += 1
                            raise RuntimeError("capture_gap: 마이크 입력 큐가 넘쳤습니다.")
                        finally:
                            write_event(log, row)

                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while stats["captured_frames"] < limit and not stop.is_set():
                            if stop_signal and stop_signal.exists():
                                stats["stop_reason"] = "requested"
                                break
                            if (time.perf_counter_ns() - origin) / 1e9 > seconds + 5:
                                raise TimeoutError(
                                    "마이크 입력이 제한 시간 안에 도착하지 않았습니다."
                                )
                            if not selector.select(0.1):
                                continue
                            data = os.read(process.stdout.fileno(), 3200)
                            if not data:
                                raise RuntimeError(
                                    "마이크 장치가 입력 도중 종료됐습니다. capture.log를 확인하세요."
                                )
                            if (
                                "overrun!!!"
                                in (output / "capture.log").read_text(errors="replace").lower()
                            ):
                                raise RuntimeError("ALSA 하드웨어 입력 넘침이 관측됐습니다.")
                            pending.extend(data)
                            while len(pending) >= 3200 and stats["captured_frames"] < limit:
                                count = min(3200, (limit - stats["captured_frames"]) * 2)
                                publish(bytes(pending[:count]))
                                del pending[:count]
                        if pending and stats["captured_frames"] < limit and not stop.is_set():
                            count = min(
                                len(pending) // 2 * 2, (limit - stats["captured_frames"]) * 2
                            )
                            if count:
                                publish(bytes(pending[:count]))
                        stats["stop_reason"] = stats["stop_reason"] or "duration_limit"
        except Exception as error:
            stats["error"] = str(error)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
            log = output / "capture.log"
            text = log.read_text(errors="replace") if log.exists() else ""
            stats["hardware_overruns"] = len(re.findall("overrun!!!", text, re.I))
            if stats["hardware_overruns"]:
                stats["error"] = "ALSA 하드웨어 입력 넘침이 관측됐습니다. capture.log를 확인하세요."

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    updated = 0
    try:
        while thread.is_alive() or not messages.empty():
            try:
                start, pcm = messages.get(timeout=0.1)
            except queue.Empty:
                pcm = None
            if pcm is not None:
                accept(pcm, start, start + len(pcm))
                stats["processed_chunks"] += 1
            if time.perf_counter() - updated >= 0.5:
                telemetry.update(stats)
                if on_update:
                    on_update()
                updated = time.perf_counter()
        if stats["error"]:
            raise RuntimeError(stats["error"])
        if not stats["captured_frames"]:
            raise RuntimeError("마이크 입력이 비어 있습니다.")
        telemetry.update(stats, complete=True, producer_complete=True)
        return load_audio(target, 16000)
    finally:
        stop.set()
        thread.join(3)
        telemetry.update(stats)
        if thread.is_alive():
            raise RuntimeError("마이크 수집 종료 실패")
