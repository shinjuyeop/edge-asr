"""Linux 프로세스 CPU/RSS 표본 수집. 모델/평가 실행기에 의존하지 않는다."""

import os
import time
from pathlib import Path
from threading import Event, Thread


def process_sample(pid: int) -> dict | None:
    try:
        # comm 필드에는 공백과 괄호가 포함될 수 있으므로 마지막 ')' 뒤에서 분리.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {
            "cpu_s": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
            "rss_mib": int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / (1024**2),
        }
    except (OSError, ValueError, IndexError):
        return None


class RssSampler:
    """주 프로세스 RSS 관측. 샘플 간 피크를 보증하지 않는다."""

    def __init__(self, *, sample_device=False):
        self.stop_event = Event()
        self.samples = []
        self.sample_device = sample_device
        self.thread = Thread(target=self._run, daemon=True)

    def capture(self):
        sample = process_sample(os.getpid())
        row = {"monotonic_s": time.perf_counter(), "rss_mib": sample["rss_mib"] if sample else None}
        if self.sample_device:
            for key, path in (
                ("temperature_millicelsius", "/sys/class/thermal/thermal_zone0/temp"),
                ("cpu0_freq_khz", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),
            ):
                try:
                    row[key] = int(Path(path).read_text().strip())
                except (OSError, ValueError):
                    row[key] = None
        self.samples.append(row)

    def _run(self):
        while not self.stop_event.wait(0.1):
            self.capture()

    def __enter__(self):
        self.capture()
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join()
        self.capture()

    def metadata(self):
        observed = [sample["rss_mib"] for sample in self.samples if sample["rss_mib"] is not None]
        return {
            "sample_interval_s": 0.1,
            "rss_observed_max_mib": max(observed, default=None),
            "rss_sample_count": len(observed),
            "rss_missing_count": len(self.samples) - len(observed),
            "rss_samples": self.samples,
        }
