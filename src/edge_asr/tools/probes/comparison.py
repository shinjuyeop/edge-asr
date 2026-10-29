"""RP5 통제 batch 비교. 기존 실행기를 별도 프로세스로 호출하고 원시 기록을 보존한다."""

import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.artifacts import write_json as save
from edge_asr.config import load_evaluation_config
from edge_asr.diagnostics import observe_threads

FAMILIES = ("zipformer", "sensevoice")
LIMIT_BYTES = 4 * 1024**3


def now():
    return datetime.now(timezone.utc).isoformat()


def plan_matrix():
    # 모델 교차 + 블록별 순서 변경. 3회는 3개 독립 프로세스이지 새 발화 150개가 아니다.
    orders = [
        (
            ("zipformer", 1),
            ("sensevoice", 1),
            ("zipformer", 2),
            ("sensevoice", 2),
            ("zipformer", 4),
            ("sensevoice", 4),
        ),
        (
            ("sensevoice", 2),
            ("zipformer", 4),
            ("sensevoice", 4),
            ("zipformer", 1),
            ("sensevoice", 1),
            ("zipformer", 2),
        ),
        (
            ("sensevoice", 4),
            ("zipformer", 4),
            ("sensevoice", 2),
            ("zipformer", 2),
            ("sensevoice", 1),
            ("zipformer", 1),
        ),
    ]
    return [
        {"family": family, "num_threads": threads, "repetition": block}
        for block, order in enumerate(orders, 1)
        for family, threads in order
    ]


def configuration(root, family, threads, destination):
    if family not in FAMILIES or threads not in (1, 2, 4):
        raise ValueError("허용된 모델과 1/2/4 스레드가 필요합니다.")
    source = root / "configs" / f"{family}_eval.toml"
    original = load_evaluation_config(source)
    if original["evaluation"]["mode"] != "batch" or original["evaluation"]["warmup_runs"] != 3:
        raise ValueError("batch / 워밍업 3회 설정이 필요합니다.")
    text, count = re.subn(
        r"(?m)^num_threads\s*=\s*\d+\s*$", f"num_threads = {threads}", source.read_text()
    )
    if count != 1:
        raise ValueError("num_threads 항목 하나를 찾을 수 없습니다.")
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(text)
    derived = load_evaluation_config(destination)
    expected = json.loads(json.dumps(original))
    expected["model"]["num_threads"] = threads
    if derived != expected:
        raise ValueError("스레드 이외의 설정이 변경됐습니다.")
    return {
        "source": str(source),
        "source_sha256": digest(source),
        "derived_sha256": digest(destination),
    }


def device(boundary=False):
    row = {"at_utc": now(), "monotonic_s": time.monotonic()}
    for key, path in (
        ("temperature_millicelsius", "/sys/class/thermal/thermal_zone0/temp"),
        ("cpu0_freq_khz", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),
        ("governor", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        ("loadavg", "/proc/loadavg"),
    ):
        try:
            value = Path(path).read_text().strip()
            row[key] = int(value) if key in ("temperature_millicelsius", "cpu0_freq_khz") else value
        except (OSError, ValueError):
            row[key] = None
    if boundary:
        try:
            result = subprocess.run(
                ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5
            )
            row["throttled"] = result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            row["throttled"] = None
    return row


def cool_down(output, *, threshold=55000, budget=300):
    start = time.monotonic()
    with (output / "cooldown.jsonl").open("x") as handle:
        while True:
            row = device()
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            value = row["temperature_millicelsius"]
            if value is None:
                raise RuntimeError("온도를 읽지 못해 통제 실행을 중단합니다.")
            if value <= threshold:
                return time.monotonic() - start
            if time.monotonic() - start >= budget:
                raise RuntimeError("300초 내 시작 온도 55°C 이하에 도달하지 못했습니다.")
            time.sleep(5)


def execute(root, output, item, manifest, diagnostic=False):
    output.mkdir()
    record = {
        **item,
        "status": "preparing",
        "started_at_utc": now(),
        "diagnostic_only": diagnostic,
        "limits": {
            "timeout_s": 600,
            "kill_after_s": 5,
            "address_space_bytes": LIMIT_BYTES,
            "core_bytes": 0,
            "rss_cap": None,
            "memory_cgroup_enforced": False,
        },
        "temperature_start_max_millicelsius": 55000,
        "device_sample_interval_s": 1,
        "thread_observation": None,
        "limit_verified": False,
    }
    receipt = output / "execution.json"
    save(receipt, record)
    try:
        record["configuration"] = configuration(
            root, item["family"], item["num_threads"], output / "config.toml"
        )
        record["cooldown_s"] = cool_down(output)
        command = [
            "timeout",
            "--signal=TERM",
            "--kill-after=5s",
            "600s",
            "prlimit",
            f"--as={LIMIT_BYTES}",
            "--core=0",
            "--",
            sys.executable,
            "-u",
            str(root / "scripts/edge_asr.py"),
            "evaluate",
            "--root",
            str(root),
            "--config",
            str(output / "config.toml"),
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output / "evaluations"),
        ]
        record.update(command=command, before=device(True), status="running")
        save(receipt, record)
        with (
            (output / "console.log").open("x") as console,
            (output / "device.jsonl").open("x") as monitor,
        ):
            process = subprocess.Popen(
                command,
                cwd=root,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=console,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                while process.poll() is None:
                    monitor.write(json.dumps(device()) + "\n")
                    monitor.flush()
                    paths = list((output / "evaluations").glob("eval-*/manifest.json"))
                    if len(paths) == 1:
                        try:
                            info = json.loads(paths[0].read_text())
                            pid = info["process_id"]
                            if not record["limit_verified"]:
                                limits = Path(f"/proc/{pid}/limits").read_text()
                                if not re.search(
                                    rf"Max address space\s+{LIMIT_BYTES}\s+{LIMIT_BYTES}\s+bytes",
                                    limits,
                                ):
                                    raise RuntimeError("실제 ASR PID의 주소 공간 제한 불일치")
                                record.update(limit_verified=True, asr_pid=pid, limits_text=limits)
                                save(receipt, record)
                            progress = paths[0].parent / "progress.jsonl"
                            if (
                                diagnostic
                                and record["thread_observation"] is None
                                and progress.exists()
                                and progress.stat().st_size
                            ):
                                # 첫 측정 파일 이후 3초. 파일 사이 기록/I/O도 포함하는 별도 진단 구간.
                                observation = observe_threads(pid, 3.0, 0.1)
                                save(output / "threads.json", observation)
                                record["thread_observation"] = "threads.json"
                        except (FileNotFoundError, json.JSONDecodeError, KeyError):
                            pass
                    time.sleep(1)
                record["exit_code"] = process.wait()
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
        record["status"] = {0: "completed", 124: "timeout", 137: "killed_cause_unverified"}.get(
            record["exit_code"], "failed"
        )
        runs = list((output / "evaluations").glob("eval-*"))
        record["run_directory"] = str(runs[0]) if len(runs) == 1 else None
        if record["status"] == "completed" and (
            not record["limit_verified"] or (diagnostic and record["thread_observation"] is None)
        ):
            record["status"] = "measurement_incomplete"
    except (Exception, KeyboardInterrupt) as error:
        record.update(status="controller_failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        record.update(finished_at_utc=now(), after=device(True))
        save(receipt, record)
    print(f"{output.name}: {record['status']}", flush=True)
    return record


def freeze_selection(campaign, output):
    if output.exists():
        raise ValueError("기존 선택 기록을 덮어쓰지 않습니다.")
    plan = json.loads((campaign / "plan.json").read_text())
    if plan["phase"] != "matrix" or plan["runs"] != plan_matrix():
        raise ValueError("전체 18회 행렬 실험이 필요합니다.")
    groups, signatures, input_signature = {}, {}, None
    for number, item in enumerate(plan["runs"], 1):
        directory = campaign / f"{number:02d}-{item['family']}-t{item['num_threads']}"
        execution = json.loads((directory / "execution.json").read_text())
        if (
            execution["status"] != "completed"
            or execution["diagnostic_only"]
            or not execution["limit_verified"]
        ):
            raise ValueError("실패/미계측 실행을 제외하고 설정을 선택할 수 없습니다.")
        runs = list((directory / "evaluations").glob("eval-*"))
        run = runs[0] if len(runs) == 1 else None
        summary = json.loads((run / "summary.json").read_text())["overall"]
        info = json.loads((run / "manifest.json").read_text())
        if not summary["complete"] or info["warmup"]["successful"] != 3:
            raise ValueError("완료되지 않은 평가입니다.")
        predictions = [
            json.loads(line) for line in (run / "predictions.jsonl").read_text().splitlines()
        ]
        signature = [
            (p["id"], p["reference"], p["audio"]["sha256"], p["hypothesis"]) for p in predictions
        ]
        inputs = [p[:3] for p in signature]
        if input_signature is not None and inputs != input_signature:
            raise ValueError("모델 간 평가 입력이 다릅니다.")
        input_signature = inputs
        family = item["family"]
        if family in signatures and signatures[family] != signature:
            raise ValueError("반복/스레드 간 입력 또는 가설이 다릅니다. 자동 선택을 중단합니다.")
        signatures[family] = signature
        groups.setdefault((family, item["num_threads"]), []).append(summary["rtf"])
    candidates, selected = [], {}
    for family in FAMILIES:
        medians = {threads: statistics.median(groups[(family, threads)]) for threads in (1, 2, 4)}
        best = min(medians.values())
        # 사전 정책: 최저 중앙 RTF의 5% 이내이면 적은 설정 스레드를 우선.
        selected[family] = min(
            threads for threads, value in medians.items() if value <= best * 1.05
        )
        candidates.extend(
            {
                "family": family,
                "num_threads": threads,
                "rtf_runs": groups[(family, threads)],
                "median_rtf": value,
            }
            for threads, value in medians.items()
        )
    value = {
        "frozen_at_utc": now(),
        "matrix_plan_sha256": digest(campaign / "plan.json"),
        "policy": "동일 가설/3회 성공 필수; 중앙 corpus RTF 최저의 5% 이내에서 설정 스레드가 적은 구성",
        "purpose": "외부 batch 평가 후보. Hybrid 동시 실행의 최적 설정이 아님.",
        "candidates": candidates,
        "selected": selected,
    }
    with output.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("matrix", "diagnostics", "selected", "select"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/evaluation/kss-dev50/manifest.jsonl")
    )
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--matrix", type=Path)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if args.phase == "select":
        print(
            json.dumps(
                freeze_selection(args.matrix.resolve(), output), ensure_ascii=False, indent=2
            )
        )
        return 0
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip():
        raise ValueError("추적/미추적 소스 변경을 먼저 정리해야 합니다.")
    manifest = (root / args.manifest).resolve()
    if args.phase == "selected":
        selection = json.loads(args.selection.read_text())
        runs = [
            {"family": family, "num_threads": selection["selected"][family], "repetition": 1}
            for family in FAMILIES
        ]
    else:
        runs = plan_matrix() if args.phase == "matrix" else plan_matrix()[:6]
    output.mkdir(parents=True, exist_ok=False)
    plan = {
        "schema_version": 1,
        "phase": args.phase,
        "created_at_utc": now(),
        "runs": runs,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "manifest": str(manifest),
        "manifest_sha256": digest(manifest),
        "selection_sha256": digest(args.selection) if args.selection else None,
        "selection_policy": "3회 동일 가설·성공; 최저 중앙 RTF의 5% 이내이면 설정 스레드 적은 쪽",
        "thread_environment": {
            name: os.environ.get(name)
            for name in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OMP_WAIT_POLICY",
            )
        },
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "device_monitor_scope": "외부 관찰 프로세스 1초 sysfs/proc 표본, ASR CPU에는 제외. 시간 간 피크 보증 없음.",
        "thread_diagnostic_scope": "별도 실행, 첫 파일 측정 이후 3초. 모델 로딩/워밍업 제외, 파일 사이 I/O 포함. 주요 속도 비교에서 제외.",
    }
    save(output / "plan.json", plan)
    failed = False
    for number, item in enumerate(runs, 1):
        result = execute(
            root,
            output / f"{number:02d}-{item['family']}-t{item['num_threads']}",
            item,
            manifest,
            args.phase == "diagnostics",
        )
        failed = failed or result["status"] != "completed"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
