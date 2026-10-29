"""보존된 Zipformer 프로브의 반복 실행·집계. 추론 라이브러리는 자식에서만 사용."""

import argparse
import json
import math
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

# 이전 import 경로를 쓰는 호출자에게도 같은 함수를 제공한다.
from edge_asr.artifacts import digest, write_json
from edge_asr.metrics import distribution
from edge_asr.resources import process_sample

try:
    import resource
except ImportError:  # Windows에서도 로그 파싱·통계 모듈은 import 가능.
    resource = None

MODEL = Path("models/sherpa-onnx-streaming-zipformer-korean-2024-06-16")
MODEL_FILES = (
    "tokens.txt",
    "encoder-epoch-99-avg-1.int8.onnx",
    "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.int8.onnx",
    "test_wavs/0.wav",
)
METRICS = {
    "Audio duration": "audio_duration_s",
    "First partial": "first_partial_from_audio_start_s",
    "Final elapsed": "final_elapsed_s",
    "Finalization delay": "finalization_from_audio_end_s",
    "Max processing overrun": "max_processing_overrun_ms",
}


def schedule(threads: list[int], warmups: int, repetitions: int) -> list[dict]:
    rows = []
    for phase, count in (("warmup", warmups), ("measurement", repetitions)):
        for round_index in range(count):
            offset = round_index % len(threads)
            for thread_count in threads[offset:] + threads[:offset]:
                rows.append({"phase": phase, "round": round_index + 1, "threads": thread_count})
    return rows


def parse_probe(text: str) -> dict:
    marker = "--- Summary ---"
    if text.count(marker) != 1:
        raise ValueError("프로브 Summary가 정확히 하나 필요합니다.")
    lines = text.split(marker)[1].splitlines()
    fields = {}
    for line in lines:
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in fields:
                raise ValueError(f"중복 Summary 항목: {key}")
            fields[key] = value.strip()
    metrics = {}
    for label, name in METRICS.items():
        value = fields.get(label)
        if label == "First partial" and value == "None":
            metrics[name] = None
            continue
        if value is None:
            raise ValueError(f"Summary 항목 누락: {label}")
        number = float(value.removesuffix("ms").removesuffix("s"))
        if not math.isfinite(number):
            raise ValueError(f"유한하지 않은 값: {label}")
        if number < 0 and label != "Finalization delay":
            raise ValueError(f"음수인 값: {label}")
        metrics[name] = number
    if metrics["audio_duration_s"] <= 0 or "Final result" not in fields:
        raise ValueError("오디오 길이 또는 최종 결과가 유효하지 않습니다.")
    if (
        abs(
            metrics["final_elapsed_s"]
            - metrics["audio_duration_s"]
            - metrics["finalization_from_audio_end_s"]
        )
        > 0.002
    ):
        raise ValueError("Summary 시간 관계가 맞지 않습니다.")
    partial = metrics["first_partial_from_audio_start_s"]
    if partial is not None and partial > metrics["final_elapsed_s"] + 0.001:
        raise ValueError("첫 부분 출력이 최종 결과보다 늦습니다.")
    return {**metrics, "final_text": fields["Final result"]}


def read_assets(root: Path) -> dict:
    hashes = {str(MODEL / name): digest(root / MODEL / name) for name in MODEL_FILES}
    with wave.open(str(root / MODEL / "test_wavs/0.wav"), "rb") as audio:
        if (
            audio.getframerate(),
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getcomptype(),
        ) != (16000, 1, 2, "NONE"):
            raise ValueError("원본 프로브는 16kHz mono PCM16 WAV만 지원합니다.")
        frames = audio.getnframes()
    if frames <= 0:
        raise ValueError("빈 WAV는 측정할 수 없습니다.")
    return {
        "sha256": hashes,
        "frames": frames,
        "sample_rate_hz": 16000,
        "duration_s": frames / 16000,
    }


def target_environment(python: str, log: Path) -> dict:
    code = """
import json, platform, sys
from importlib.metadata import distributions
if sys.version_info[:2] != (3, 11):
    raise RuntimeError("이 실험의 기준 인터프리터는 Python 3.11입니다.")
import numpy, sherpa_onnx
print(json.dumps({"python": sys.version, "executable": sys.executable,
    "prefix": sys.prefix, "base_prefix": sys.base_prefix,
    "machine": platform.machine(), "platform": platform.platform(),
    "packages": sorted([{"name": d.metadata["Name"], "version": d.version}
                        for d in distributions()], key=lambda d: d["name"] or "")}))
"""
    result = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"대상 Python의 NumPy/sherpa-onnx 검사 실패. 로그: {log}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise ValueError(f"환경 정보 JSON을 읽지 못했습니다. 로그: {log}") from error


def system_snapshot() -> dict:
    values = {}
    for pattern in (
        "class/thermal/thermal_zone*/temp",
        "devices/system/cpu/cpufreq/policy*/scaling_governor",
        "devices/system/cpu/cpufreq/policy*/scaling_cur_freq",
    ):
        for path in Path("/sys").glob(pattern):
            try:
                values[str(path)] = path.read_text().strip()
            except OSError:
                pass
    return {"sysfs_raw": values, "throttling": None}


def stop_process(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_one(
    python: str,
    probe: Path,
    asset_root: Path,
    folder: Path,
    job: dict,
    timeout_s: float,
    sample_interval_s: float,
) -> dict:
    folder.mkdir()
    result = {
        **job,
        "log": str(folder.name + "/probe.txt"),
        "metrics": {},
        "status": "launch_error",
        "returncode": None,
        "error": None,
    }
    rss_values, cpu_values = [], []
    previous = None
    sampled = 0
    missing = 0
    started = time.perf_counter()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = None
    try:
        with (
            (folder / "probe.txt").open("x", encoding="utf-8") as output,
            (folder / "resources.jsonl").open("x", encoding="utf-8") as resources,
        ):
            process = subprocess.Popen(
                [python, "-u", str(probe)],
                cwd=asset_root,
                env={**os.environ, "ASR_THREADS": str(job["threads"]), "PYTHONIOENCODING": "utf-8"},
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            result["status"] = "completed"
            try:
                while True:
                    now = time.perf_counter()
                    sample = process_sample(process.pid)
                    if sample is not None:
                        sampled += 1
                        rss_values.append(sample["rss_mib"])
                        cpu_percent = None
                        if previous is not None and now > previous[0]:
                            cpu_percent = max(
                                0.0, (sample["cpu_s"] - previous[1]) / (now - previous[0]) * 100
                            )
                            cpu_values.append(cpu_percent)
                        previous = (now, sample["cpu_s"])
                        resources.write(
                            json.dumps(
                                {"elapsed_s": now - started, **sample, "cpu_percent": cpu_percent}
                            )
                            + "\n"
                        )
                    else:
                        missing += 1
                    if process.poll() is not None:
                        break
                    if now - started >= timeout_s:
                        result["status"] = "timeout"
                        stop_process(process)
                        break
                    time.sleep(
                        min(sample_interval_s, max(0, timeout_s - (time.perf_counter() - started)))
                    )
            finally:
                if process.poll() is None:
                    stop_process(process)
    except KeyboardInterrupt:
        result["status"] = "interrupted"
    except OSError as error:
        result["error"] = str(error)
        result["status"] = "launch_error"
    finally:
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        elapsed = time.perf_counter() - started
        result["process_wall_s"] = elapsed
        result["returncode"] = process.returncode if process is not None else None
        result["resources"] = {
            "process_cpu_percent": (
                (after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime)
                / elapsed
                * 100
            )
            if process is not None
            else None,
            "main_cpu_percent_samples": distribution(cpu_values),
            "main_rss_mib_samples": distribution(rss_values),
            "sample_count": sampled,
            "missed_samples": missing,
        }
    if result["status"] == "completed":
        if result["returncode"] != 0:
            result["status"] = "failed"
            result["error"] = "자식 프로세스가 비정상 종료했습니다. probe.txt를 확인하세요."
        else:
            try:
                result["metrics"] = parse_probe((folder / "probe.txt").read_text(encoding="utf-8"))
                result["status"] = "success"
            except (ValueError, UnicodeError) as error:
                result["status"] = "invalid_output"
                result["error"] = str(error)
    write_json(folder / "result.json", result)
    return result


def summarize(rows: list[dict], threads: list[int], repetitions: int) -> dict:
    groups = {}
    for count in threads:
        attempted = [r for r in rows if r["phase"] == "measurement" and r["threads"] == count]
        good = [r for r in attempted if r["status"] == "success"]
        metrics = {}
        for name in list(METRICS.values())[1:]:
            metrics[name] = distribution(
                [r["metrics"][name] for r in good if r["metrics"][name] is not None]
            )
        metrics["process_cpu_percent"] = distribution(
            [
                r["resources"]["process_cpu_percent"]
                for r in good
                if r["resources"]["process_cpu_percent"] is not None
            ]
        )
        metrics["sampled_main_rss_max_mib"] = distribution(
            [
                r["resources"]["main_rss_mib_samples"]["max"]
                for r in good
                if r["resources"]["main_rss_mib_samples"]["max"] is not None
            ]
        )
        groups[str(count)] = {
            "attempted": len(attempted),
            "successful": len(good),
            "planned": repetitions,
            "not_attempted": repetitions - len(attempted),
            "no_partial_count": sum(
                r["metrics"]["first_partial_from_audio_start_s"] is None for r in good
            ),
            "failed": len(attempted) - len(good),
            "metrics": metrics,
        }
    return {
        "threads": groups,
        "warmup_attempted": sum(r["phase"] == "warmup" for r in rows),
        "warmup_failed": sum(r["phase"] == "warmup" and r["status"] != "success" for r in rows),
        "unmeasured": ["cer", "aligned_recognition_latency", "drops", "backlog", "processing_rtf"],
    }


def save_summary(output: Path, rows: list[dict], threads: list[int], repetitions: int) -> None:
    summary = summarize(rows, threads, repetitions)
    write_json(output / "summary.json", summary)
    lines = [
        "# Zipformer 반복 측정 요약",
        "",
        "동일 WAV 단일 샘플의 반복 실행입니다. 워밍업은 집계에서 제외하며 실패·미실행 수를 함께 표시합니다.",
        "첫 부분 출력은 입력 대기를 포함하는 원본 프로브의 근사 시점입니다. CPU/RSS는 모델 로딩을 포함한 프로세스 범위입니다.",
        "유효 관측이 2개 미만인 분위수와 미계측 값은 `—`로 표시합니다.",
        "",
        "| 스레드 | 성공/계획 | 실패/미실행 | 첫 부분 p50/p95 (초) | 최종 지연 p50/p95 (초) | 평균 CPU (%) | 관측 최대 RSS (MiB) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    def fmt(value):
        return "—" if value is None else f"{value:.3f}"

    for count, group in summary["threads"].items():
        metrics = group["metrics"]
        partial, final = (
            metrics["first_partial_from_audio_start_s"],
            metrics["finalization_from_audio_end_s"],
        )
        lines.append(
            f"| {count} | {group['successful']}/{group['planned']} | {group['failed']}/{group['not_attempted']} | "
            f"{fmt(partial['p50'])}/{fmt(partial['p95'])} | {fmt(final['p50'])}/{fmt(final['p95'])} | "
            f"{fmt(metrics['process_cpu_percent']['mean'])} | {fmt(metrics['sampled_main_rss_max_mib']['max'])} |"
        )
    lines.extend(
        [
            "",
            f"워밍업 시도 {summary['warmup_attempted']}회, 실패 {summary['warmup_failed']}회.",
            "",
            "부분 가설 부재 수, 지표별 유효 관측 수 및 미계측 항목은 summary.json을 확인하세요.",
            "",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    repo = Path.cwd()
    parser = argparse.ArgumentParser(
        description="기존 Zipformer 프로브 반복 측정 (Linux/Python 3.11)"
    )
    parser.add_argument(
        "--asset-root", type=Path, required=True, help="기존 models/를 포함하는 프로젝트 루트"
    )
    parser.add_argument(
        "--python", default=sys.executable, help="기존 RP5 가상환경의 Python 절대 경로"
    )
    parser.add_argument(
        "--probe", type=Path, default=repo / "experiments/m1_zipformer/streaming_probe.py"
    )
    parser.add_argument("--output-dir", type=Path, default=repo / "benchmarks/runs")
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--warmups", type=int, default=3, help="스레드별 별도 워밍업 프로세스 수")
    parser.add_argument("--repetitions", type=int, default=30, help="스레드별 측정 실행 수")
    parser.add_argument("--timeout-s", type=float, default=120)
    parser.add_argument("--sample-interval-ms", type=float, default=100)
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or resource is None:
        parser.error("CPU/RSS 수집은 Linux에서 지원합니다. Host는 WSL을 사용할 수 있습니다.")
    if (
        not args.threads
        or any(n < 1 for n in args.threads)
        or len(set(args.threads)) != len(args.threads)
        or args.warmups < 0
        or args.repetitions < 1
        or not math.isfinite(args.timeout_s)
        or args.timeout_s <= 0
        or not math.isfinite(args.sample_interval_ms)
        or args.sample_interval_ms <= 0
    ):
        parser.error(
            "스레드는 중복 없는 양수, 워밍업은 0 이상, 반복 수·시간 간격은 양수여야 합니다."
        )
    # resolve()는 venv Python 심볼릭 링크를 풀어 가상환경을 잃으므로 사용하지 않음.
    python = os.path.abspath(os.path.expanduser(args.python))
    asset_root, probe = args.asset_root.expanduser().resolve(), args.probe.expanduser().resolve()
    if not Path(python).is_file() or not probe.is_file() or not asset_root.is_dir():
        parser.error("Python·프로브·자산 루트 경로를 확인하세요.")
    args.output_dir = args.output_dir.expanduser()
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        prefix = datetime.now(timezone.utc).strftime("zipformer-%Y%m%dT%H%M%SZ-")
        output = Path(tempfile.mkdtemp(prefix=prefix, dir=args.output_dir)).resolve()
    except OSError as error:
        parser.error(f"새 결과 폴더를 만들 수 없습니다: {error}")
    print(f"결과 폴더: {output}", flush=True)
    plan = schedule(args.threads, args.warmups, args.repetitions)
    rows = []
    manifest = {
        "schema_version": 1,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "paced_stream_subprocess",
        "unique_audio_samples": 1,
        "arguments": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "probe": {"path": str(probe), "sha256": None},
        "python_executable": python,
        "asset_root": str(asset_root),
        "runner_sha256": None,
        "schedule": plan,
        "resource_scope": "CPU: 자식 및 회수된 자손, 부모가 관측한 시작~종료감지 시간 기준(폴링 지연 포함). RSS/CPU 표본: 자식 주 PID. 모델 로딩·WAV 읽기·입력 대기 포함, 부모 제외.",
        "warmup_scope": "매 실행 새 프로세스·모델. 별도 워밍업은 시스템/파일 캐시 예열이며 모델 상주 워밍업 아님.",
        "status": "running",
        "system_before": system_snapshot(),
    }
    manifest["source_commit"] = None
    write_json(output / "manifest.json", manifest)
    code = 0
    try:
        manifest["probe"]["sha256"] = digest(probe)
        manifest["runner_sha256"] = digest(Path(__file__))
        if (repo / ".git").exists():
            try:
                revision = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                manifest["source_commit"] = (
                    revision.stdout.strip() if revision.returncode == 0 else None
                )
            except OSError:
                pass
        environment = target_environment(python, output / "environment-check.txt")
        write_json(output / "environment.json", environment)
        manifest["assets"] = read_assets(asset_root)
        write_json(output / "manifest.json", manifest)
        for index, job in enumerate(plan, 1):
            name = f"{index:03d}-{job['phase']}-t{job['threads']}-r{job['round']:03d}"
            print(f"[{index}/{len(plan)}] {name}", flush=True)
            row = run_one(
                python,
                probe,
                asset_root,
                output / name,
                job,
                args.timeout_s,
                args.sample_interval_ms / 1000,
            )
            if (
                row["status"] == "success"
                and abs(row["metrics"]["audio_duration_s"] - manifest["assets"]["duration_s"])
                > 0.000501
            ):
                row["status"] = "invalid_output"
                row["error"] = "WAV 프레임 수와 프로브의 오디오 길이가 다릅니다."
                write_json(output / name / "result.json", row)
            rows.append(row)
            write_json(output / "runs.json", rows)
            save_summary(output, rows, args.threads, args.repetitions)
            print(f"  {row['status']}", flush=True)
            if row["status"] != "success":
                code = 130 if row["status"] == "interrupted" else 1
                if job["phase"] == "warmup" or row["status"] == "interrupted":
                    break
        manifest["status"] = (
            "interrupted" if code == 130 else "completed" if code == 0 else "incomplete_or_failed"
        )
    except KeyboardInterrupt:
        manifest["status"], code = "interrupted", 130
    except (OSError, ValueError, subprocess.SubprocessError, wave.Error) as error:
        manifest["status"], code = "preflight_or_runner_error", 2
        manifest["error"] = str(error)
        print(f"오류: {error}", file=sys.stderr)
    finally:
        manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["system_after"] = system_snapshot()
        write_json(output / "manifest.json", manifest)
        write_json(output / "runs.json", rows)
        save_summary(output, rows, args.threads, args.repetitions)
    print(f"요약: {output / 'summary.md'} 및 summary.json", flush=True)
    return code
