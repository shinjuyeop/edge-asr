"""냉각 계측을 읽기만 한다. 과거 이력·현재 제한·프로젝트 중단 기준을 분리한다."""

import json
import math
import platform
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl
from edge_asr.thermal import sample


def read_value(path, convert=str):
    try:
        return {"value": convert(path.read_text().strip().rstrip("\0")), "error": None}
    except (OSError, ValueError) as error:
        return {"value": None, "error": type(error).__name__}


def inventory(sysfs=Path("/sys")):
    """기기의 값만 열거한다. 결측을 팬 정지나 0°C로 대체하지 않는다."""
    zones, devices, fans, cpus = [], [], [], []
    for zone in sorted((sysfs / "class/thermal").glob("thermal_zone*")):
        zones.append(
            {
                "path": str(zone),
                "type": read_value(zone / "type"),
                "temperature_mc": read_value(zone / "temp", int),
                "trips": [
                    {
                        "name": trip.name,
                        "temperature_mc": read_value(trip, int),
                        "type": read_value(trip.with_name(trip.name.replace("_temp", "_type"))),
                        "hysteresis_mc": read_value(
                            trip.with_name(trip.name.replace("_temp", "_hyst")), int
                        ),
                    }
                    for trip in sorted(zone.glob("trip_point_*_temp"))
                ],
            }
        )
    for device in sorted((sysfs / "class/thermal").glob("cooling_device*")):
        devices.append(
            {
                "path": str(device),
                "type": read_value(device / "type"),
                "current_state": read_value(device / "cur_state", int),
                "max_state": read_value(device / "max_state", int),
            }
        )
    for hwmon in sorted((sysfs / "class/hwmon").glob("hwmon*")):
        for fan in sorted(hwmon.glob("fan*_input")):
            fans.append(
                {
                    "path": str(fan),
                    "driver_name": read_value(hwmon / "name"),
                    "rpm": read_value(fan, int),
                    "pwm1": read_value(hwmon / "pwm1", int),
                    "pwm1_enable": read_value(hwmon / "pwm1_enable", int),
                }
            )
    for cpu in sorted((sysfs / "devices/system/cpu").glob("cpu[0-9]*/cpufreq")):
        cpus.append(
            {
                "path": str(cpu),
                **{
                    name: read_value(cpu / name, convert)
                    for name, convert in (
                        ("scaling_governor", str),
                        ("scaling_cur_freq", int),
                        ("scaling_min_freq", int),
                        ("scaling_max_freq", int),
                    )
                },
            }
        )
    return {
        "device_model": read_value(sysfs / "firmware/devicetree/base/model"),
        "thermal_zones": zones,
        "cooling_devices": devices,
        "fans": fans,
        "fan_sensor_status": "exposed" if fans else "unavailable",
        "cpu_frequency": cpus,
        "scope": "읽기 전용 순간 표본. 팬 RPM은 방열판 접촉·통풍·장기 냉각 성능을 보증하지 않음.",
    }


def live_snapshot():
    return {
        "host": platform.node(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "telemetry": sample(),
        "inventory": inventory(),
        "scope": "이 명령이 실행된 기기만 관찰. Host에서 실행한 값은 RP5 실측이 아님.",
    }


def summarize_history(folder):
    receipt_path, trace_path = folder / "execution.json", folder / "thermal.jsonl"
    receipt = json.loads(receipt_path.read_text())
    trace = read_jsonl(trace_path)
    if not trace:
        raise ValueError("과거 열 표본이 없습니다.")
    before, after = receipt["before"], receipt.get("after")
    # before/after는 부하 감시 구간 밖의 표본임을 아래에서 따로 명시한다.
    rows = [before, *trace, *([after] if after else [])]
    clocks = [r["monotonic_s"] for r in rows]
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in clocks) or any(
        a > b for a, b in zip(clocks, clocks[1:])
    ):
        raise ValueError("열 표본 시계 순서/유한성 오류")
    for row in rows:
        for key in (
            "temperature_mc",
            "cpu0_freq_khz",
            "fan_rpm",
            "current_limits",
            "history_limits",
        ):
            if row.get(key) is not None and (type(row[key]) is not int or row[key] < 0):
                raise ValueError("잘못된 과거 계측값: " + key)
    stop = receipt["policy"]["stop_mc"]
    if type(stop) is not int or stop <= 0:
        raise ValueError("기록된 프로젝트 온도 중단 기준 오류")

    def point(row):
        return {
            "at_utc": row["at_utc"],
            "elapsed_from_before_s": row["monotonic_s"] - before["monotonic_s"],
            "temperature_c": row.get("temperature_mc") / 1000
            if row.get("temperature_mc") is not None
            else None,
            "fan_rpm": row.get("fan_rpm"),
            "cpu0_freq_khz": row.get("cpu0_freq_khz"),
            "current_limits": row.get("current_limits"),
            "history_limits": row.get("history_limits"),
        }

    def first(predicate):
        return next((point(row) for row in rows if predicate(row)), None)

    def maximum(key, selected=rows):
        return max((r[key] for r in selected if r.get(key) is not None), default=None)

    observed_current = [r["current_limits"] for r in rows if r.get("current_limits") is not None]
    history = before.get("history_limits")
    new_history = 0 if all(r.get("history_limits") is not None for r in rows) else None
    if new_history is not None:
        for row in rows:
            if row.get("history_limits") is not None:
                new_history |= row["history_limits"] & ~history
    return {
        "run": str(folder),
        "source_sha256": {
            "execution.json": digest(receipt_path),
            "thermal.jsonl": digest(trace_path),
        },
        "status": receipt["status"],
        "control_valid": receipt["control_valid"],
        "rejection": receipt.get("rejection"),
        "thermal_sample_count": len(trace),
        "before": point(before),
        "after": point(after) if after else None,
        "software_stop_mc": stop,
        "first_observed_software_temperature_limit": first(
            lambda r: r.get("temperature_mc") is not None and r["temperature_mc"] >= stop
        ),
        "first_observed_current_limit": first(lambda r: bool(r.get("current_limits"))),
        "current_limit_observed": any(observed_current) if observed_current else None,
        "new_history_bits": new_history,
        "telemetry_missing_samples": sum(
            any(
                r.get(k) is None
                for k in ("temperature_mc", "cpu0_freq_khz", "current_limits", "history_limits")
            )
            for r in rows
        ),
        "fan_missing_samples": sum(r.get("fan_rpm") is None for r in rows),
        "fan_positive_samples": sum((r.get("fan_rpm") or 0) > 0 for r in rows),
        "fan_rpm_max": maximum("fan_rpm"),
        "temperature_mc_max_monitor": maximum("temperature_mc", trace),
        "temperature_mc_max_with_before_after": maximum("temperature_mc"),
        "scope": "과거 표본 재분석. 실제 임계값 통과 순간은 표본 사이 미확인. 현재 제한 0은 표본 사이 제한 부재를 증명하지 않음.",
    }
