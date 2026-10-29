"""읽기 전용 RP5 온도/클럭/현재 및 누적 제한 계측과 비교 허용 조건."""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def sample():
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
    try:
        fans = list(Path("/sys/class/hwmon").glob("hwmon*/fan1_input"))
        row["fan_rpm"] = int(fans[0].read_text()) if len(fans) == 1 else None
    except (OSError, ValueError):
        row["fan_rpm"] = None
    return row


def rejection(row, initial_history=0, *, max_temperature_mc=75000):
    if any(
        row.get(key) is None
        for key in ("temperature_mc", "cpu0_freq_khz", "current_limits", "history_limits")
    ):
        return "telemetry_unavailable"
    if row["current_limits"]:
        return "current_throttling_or_voltage_limit"
    if row["history_limits"] & ~initial_history:
        return "new_limit_history_since_admission"
    if row["temperature_mc"] >= max_temperature_mc:
        return "temperature_limit"
    return None
