"""RP5 열 계측과 제한 감시. 통제 비교와 기본 열 보호 하의 운영 검사를 구분한다."""

import argparse
import json
import os
import re
import signal
import subprocess
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

from edge_asr.artifacts import write_json as save
from edge_asr.diagnostics import thread_snapshot
from edge_asr.thermal import policy_settings, rejection, sample, temperature_warning


def admit(output, *, budget_s=300):
    start, stable = time.monotonic(), 0
    with (output / "admission.jsonl").open("x") as log:
        while time.monotonic() - start < budget_s:
            row = sample()
            log.write(json.dumps(row) + "\n")
            log.flush()
            if rejection(row, row.get("history_limits") or 0) == "telemetry_unavailable":
                raise RuntimeError("telemetry_unavailable")
            stable = (
                stable + 1 if row["temperature_mc"] <= 53000 and not row["current_limits"] else 0
            )
            if stable >= 3:
                # 대기 표본과 실제 기록에 다른 온도를 사용하지 않는다.
                return row
            time.sleep(1)
    raise RuntimeError("cooling_admission_timeout")


def terminate(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def run(root, output, command, *, temperature_policy="legacy75", console_callback=None):
    settings = policy_settings(temperature_policy)
    operational = temperature_policy == "operational"
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": 1,
        "status": "preparing",
        "control_valid": False,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "policy": {
            "admission_mc": 53000,
            "admission_consecutive": 3,
            "stop_mc": 75000,
            "interval_s": 1,
            "timeout_s": 600,
            "address_space_bytes": 4294967296,
            "scope": "1Hz 표본. 고정 클럭 보장 아님. 현재 제한/새 이력/결측/75°C에서 중단.",
        },
        "limit_verified": False,
        "command": command,
    }
    receipt = output / "execution.json"
    if temperature_policy != "legacy75":
        record["policy"].update(
            name=temperature_policy,
            warn_mc=settings["warn_mc"],
            stop_mc=settings["stop_mc"],
            interval_s=settings["interval_s"],
            scope="폴링 대기 0.25초+조회 비용. 75°C 경고, 80°C/현재 제한/새 이력/결측 중단. 고정 클럭/열평형 보장 아님.",
        )
        record["warnings"] = {
            "sample_count": 0,
            "first_sample": None,
            "scope": "monitor/after 관측의 온도 경고; 현재 제한 등 중단 판정과 별도",
        }
    if operational:
        record.update(evaluation_scope="operational_sustained_load", operational_valid=False)
        record["policy"].update(
            allow_thermal_limits=True,
            firmware_thermal_protection="unchanged",
            scope="운영 검사: 80°C 이상 경고, 온도/감속만으로 중단하지 않음. 기본 열 보호 유지. 저전압/새 저전압 이력/결측/timeout/실행 실패는 중단. 통제 비교 아님.",
        )

    def reject(row):
        return rejection(
            row,
            record["before"]["history_limits"],
            max_temperature_mc=settings["stop_mc"],
            allow_thermal_limits=operational,
        )

    save(receipt, record)
    try:
        record["before"] = admit(output)
        record["status"] = "running"
        save(receipt, record)
        wrapped = [
            "timeout",
            "--signal=TERM",
            "--kill-after=5s",
            "600s",
            "prlimit",
            "--as=4294967296",
            "--core=0",
            "--",
            *command,
        ]
        with (
            (output / "console.log").open("x") as console,
            (output / "thermal.jsonl").open("x") as monitor,
            (output / "threads.jsonl").open("x") as tids,
            (output / "warnings.jsonl").open("x")
            if settings["warn_mc"] is not None
            else nullcontext(None) as warnings,
        ):

            def warn(row, phase):
                if warnings is not None and temperature_warning(row, settings["warn_mc"]):
                    warnings.write(json.dumps({"phase": phase, "sample": row}) + "\n")
                    warnings.flush()
                    record["warnings"]["sample_count"] += 1
                    if record["warnings"]["first_sample"] is None:
                        record["warnings"]["first_sample"] = row
                        save(receipt, record)

            process = subprocess.Popen(
                wrapped,
                cwd=root,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=subprocess.PIPE if console_callback else console,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            forward_failed = Event()
            forward_error = []
            reader = None
            if console_callback:

                def forward():
                    try:
                        for line in process.stdout:
                            console.write(line)
                            console.flush()
                            console_callback(line)
                    except BaseException as error:
                        forward_error.append(repr(error))
                        forward_failed.set()

                reader = Thread(target=forward, daemon=True)
                reader.start()
            reason = None
            try:
                while process.poll() is None:
                    if forward_failed.is_set():
                        raise RuntimeError("console_forward_failed: " + forward_error[0])
                    row = sample(measure_arm_clock=True) if operational else sample()
                    monitor.write(json.dumps(row) + "\n")
                    monitor.flush()
                    warn(row, "monitor")
                    reason = reject(row)
                    if reason:
                        record.update(rejection=reason, rejected_sample=row)
                        terminate(process)
                        break
                    if not record["limit_verified"]:
                        candidates = list(output.glob("evaluations/eval-*/manifest.json")) + list(
                            output.glob("worker.json")
                        )
                        for path in candidates:
                            try:
                                info = json.loads(path.read_text())
                                pid = info["process_id"]
                                limits = Path(f"/proc/{pid}/limits").read_text()
                                if not re.search(
                                    r"Max address space\s+4294967296\s+4294967296\s+bytes", limits
                                ):
                                    raise RuntimeError("address_space_limit_mismatch")
                                record.update(
                                    limit_verified=True, process_id=pid, limits_text=limits
                                )
                                save(receipt, record)
                            except (FileNotFoundError, KeyError, json.JSONDecodeError):
                                pass
                    if record["limit_verified"]:
                        try:
                            snapshot = thread_snapshot(record["process_id"])
                            snapshot["clock_ticks_per_second"] = os.sysconf("SC_CLK_TCK")
                            tids.write(json.dumps(snapshot) + "\n")
                            tids.flush()
                        except (OSError, ValueError, IndexError) as error:
                            tids.write(json.dumps({"error": repr(error)}) + "\n")
                            tids.flush()
                    time.sleep(settings["interval_s"])
                record["exit_code"] = process.wait()
            finally:
                terminate(process)
                if reader:
                    reader.join(5)
                    if reader.is_alive():
                        raise RuntimeError("console_reader_cleanup_failed")
                    process.stdout.close()
                    if forward_failed.is_set():
                        raise RuntimeError("console_forward_failed: " + forward_error[0])
            after = sample(measure_arm_clock=True) if operational else sample()
            warn(after, "after")
        reason = reason or reject(after)
        record["after"] = after
        record["status"] = (
            ("operational_rejected" if operational else "control_rejected")
            if reason
            else "completed"
            if record["exit_code"] == 0 and record["limit_verified"]
            else "failed_or_unverified"
        )
        record["control_valid"] = record["status"] == "completed" and not operational
        if operational:
            record["operational_valid"] = record["status"] == "completed"
        if reason:
            record["rejection"] = reason
    except (Exception, KeyboardInterrupt) as error:
        record.update(status="blocked", error=f"{type(error).__name__}: {error}")
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save(receipt, record)
    print(
        json.dumps(
            {
                "output": str(output),
                "status": record["status"],
                "rejection": record.get("rejection"),
                "error": record.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if record.get("operational_valid", record["control_valid"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--temperature-policy",
        choices=("legacy75", "warn75-stop80", "operational"),
        default="legacy75",
        help="legacy75: 75°C 중단, warn75-stop80: 75°C 경고/80°C 중단, operational: 기본 열 보호 아래 온도/감속을 기록하며 계속 실행",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("실행 명령이 필요합니다.")
    if subprocess.check_output(["git", "status", "--porcelain"]).strip():
        parser.error("소스 변경을 먼저 commit/push/pull 하세요.")
    raise SystemExit(
        run(Path.cwd(), args.output.resolve(), command, temperature_policy=args.temperature_policy)
    )


if __name__ == "__main__":
    raise SystemExit(main())
