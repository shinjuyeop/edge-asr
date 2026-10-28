"""이미 실행 중인 Linux PID의 TID별 CPU 관찰. 모델·스레드 설정을 변경하지 않는다."""

import math
import os
from pathlib import Path
import time


def _stat(path):
    text = path.read_text()
    left, right = text.index("("), text.rindex(")")
    fields = text[right + 2:].split()
    return {"name": text[left + 1:right], "cpu_ticks": int(fields[11]) + int(fields[12]),
            "start_ticks": int(fields[19])}


def thread_snapshot(pid, proc_root=Path("/proc")):
    directory = proc_root / str(pid)
    identity = _stat(directory / "stat")["start_ticks"]
    threads, missing = {}, 0
    for path in (directory / "task").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            threads[path.name] = _stat(path / "stat")
        except (OSError, ValueError, IndexError):
            missing += 1  # 관측 중 종료된 TID 등. 0% 사용으로 간주하지 않는다.
    return {"at_monotonic_s": time.monotonic(), "process_start_ticks": identity,
            "threads": threads, "unreadable_threads": missing}


def observe_threads(pid, duration_s=3.0, interval_s=0.1):
    if type(pid) is not int or pid < 1:
        raise ValueError("pid는 1 이상의 정수여야 합니다.")
    for value in (duration_s, interval_s):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("관측 시간과 간격은 유한한 양수여야 합니다.")
    if duration_s > 60 or interval_s < 0.05 or interval_s > duration_s:
        raise ValueError("관측 시간은 최대 60초, 간격은 0.05초 이상이며 관측 시간 이하여야 합니다.")
    first = thread_snapshot(pid)
    samples, status = [first], "completed"
    start = first["at_monotonic_s"]
    while True:
        remaining = start + duration_s - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_s, remaining))
        try:
            snapshot = thread_snapshot(pid)
        except FileNotFoundError:
            status = "process_exited"
            break
        if snapshot["process_start_ticks"] != first["process_start_ticks"]:
            status = "pid_reused"
            break
        samples.append(snapshot)
    ticks = os.sysconf("SC_CLK_TCK")
    totals = {}
    for previous, current in zip(samples, samples[1:]):
        wall = current["at_monotonic_s"] - previous["at_monotonic_s"]
        for tid, thread in current["threads"].items():
            old = previous["threads"].get(tid)
            if old is None or old["start_ticks"] != thread["start_ticks"]:
                continue
            cpu = (thread["cpu_ticks"] - old["cpu_ticks"]) / ticks
            if cpu < 0 or wall <= 0:
                raise ValueError("TID CPU 카운터·단조 시계가 역행했습니다.")
            key = (tid, thread["start_ticks"])
            item = totals.setdefault(key, {"tid": int(tid), "name": thread["name"],
                                           "thread_start_ticks": thread["start_ticks"],
                                           "observed_cpu_s": 0.0, "paired_observation_s": 0.0})
            item["observed_cpu_s"] += cpu
            item["paired_observation_s"] += wall
    for item in totals.values():
        wall = item["paired_observation_s"]
        item["mean_cpu_percent"] = item["observed_cpu_s"] / wall * 100 if wall else None
    return {"schema_version": 1, "kind": "existing_pid_thread_observation", "pid": pid,
            "status": status, "requested_duration_s": duration_s, "interval_s": interval_s,
            "observed_wall_s": samples[-1]["at_monotonic_s"] - start,
            "thread_count_observed_max": max(len(s["threads"]) for s in samples),
            "threads": sorted(totals.values(), key=lambda item: item["observed_cpu_s"], reverse=True),
            "snapshots": samples,
            "scope": "코어 하나가 100%. 연속 두 표본에 동일 시작 시각으로 존재한 TID의 CPU 차분만 포함. 생성/소멸 경계 CPU는 누락 가능.",
            "runtime_thread_roles": None,
            "runtime_thread_roles_reason": "스레드 이름/개수만으로 ORT 세션·spinning의 원인을 확정하지 않습니다."}
