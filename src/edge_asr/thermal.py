"""읽기 전용 RP5 온도/클럭/현재 및 누적 제한 계측과 비교 허용 조건."""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def policy_settings(name="legacy75"):
    """과거 재현용 기본값과 사용자가 승인한 새 실험만 허용한다."""
    if name == "legacy75":
        return {"warn_mc": None, "stop_mc": 75000, "interval_s": 1}
    if name == "warn75-stop80":
        return {"warn_mc": 75000, "stop_mc": 80000, "interval_s": 0.25}
    if name == "operational":
        return {"warn_mc": 80000, "stop_mc": None, "interval_s": 1}
    raise ValueError("지원하지 않는 열 정책입니다.")


def temperature_warning(row, threshold_mc):
    value = row.get("temperature_mc")
    return threshold_mc is not None and value is not None and value >= threshold_mc


def sample(*, measure_arm_clock=False):
    row = {"at_utc": datetime.now(timezone.utc).isoformat(), "monotonic_s": time.monotonic()}
    for key, path in (
        ("temperature_mc", "/sys/class/thermal/thermal_zone0/temp"),
        ("cpu0_freq_khz", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),
        ("governor", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
    ):
        try:
            value = Path(path).read_text().strip()
            row[key] = value if key == "governor" else int(value)
        except (OSError, ValueError):
            row[key] = None
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=1
        )
        flags = int(result.stdout.strip().split("=")[1], 16) if result.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        flags = None
    row.update(
        throttled_flags=flags,
        current_limits=flags & 15 if flags is not None else None,
        history_limits=(flags >> 16) & 15 if flags is not None else None,
    )
    if measure_arm_clock:
        try:
            result = subprocess.run(
                ["vcgencmd", "measure_clock", "arm"], capture_output=True, text=True, timeout=1
            )
            hz = int(result.stdout.strip().split("=")[1]) if result.returncode == 0 else None
            row["arm_clock_hz"] = hz if hz is not None and hz > 0 else None
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            row["arm_clock_hz"] = None
    try:
        fans = list(Path("/sys/class/hwmon").glob("hwmon*/fan1_input"))
        row["fan_rpm"] = int(fans[0].read_text()) if len(fans) == 1 else None
    except (OSError, ValueError):
        row["fan_rpm"] = None
    return row


def rejection(row, initial_history=0, *, max_temperature_mc=75000, allow_thermal_limits=False):
    if any(
        row.get(key) is None
        for key in ("temperature_mc", "cpu0_freq_khz", "current_limits", "history_limits")
    ):
        return "telemetry_unavailable"
    mask = 1 if allow_thermal_limits else 15
    if row["current_limits"] & mask:
        return (
            "current_undervoltage"
            if allow_thermal_limits
            else "current_throttling_or_voltage_limit"
        )
    if row["history_limits"] & ~initial_history & mask:
        return (
            "new_undervoltage_history"
            if allow_thermal_limits
            else "new_limit_history_since_admission"
        )
    if max_temperature_mc is not None and row["temperature_mc"] >= max_temperature_mc:
        return "temperature_limit"
    return None
