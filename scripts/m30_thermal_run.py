"""RP5의 엄격한 온도/현재 제한 감시. 조건 위반은 성공한 평가와 구분해 중단한다."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edge_asr.thermal import sample, rejection
from edge_asr.diagnostics import thread_snapshot


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def admit(output, *, budget_s=300):
    start, stable = time.monotonic(), 0
    with (output / "admission.jsonl").open("x") as log:
        while time.monotonic() - start < budget_s:
            row = sample()
            log.write(json.dumps(row) + "\n"); log.flush()
            if rejection(row, row.get("history_limits") or 0) == "telemetry_unavailable":
                raise RuntimeError("telemetry_unavailable")
            stable = stable + 1 if row["temperature_mc"] <= 53000 and not row["current_limits"] else 0
            if stable >= 3:
                # 대기 표본과 실제 기록에 다른 온도를 사용하지 않는다.
                return row
            time.sleep(1)
    raise RuntimeError("cooling_admission_timeout")


def terminate(process):
    if process.poll() is None:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: return
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait()


def run(root, output, command):
    output.mkdir(parents=True, exist_ok=False)
    record = {"schema_version": 1, "status": "preparing", "control_valid": False,
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
              "policy": {"admission_mc": 53000, "admission_consecutive": 3, "stop_mc": 75000,
                         "interval_s": 1, "timeout_s": 600, "address_space_bytes": 4294967296,
                         "scope": "1Hz 표본. 고정 클럭 보장 아님. 현재 제한/새 이력/결측/75°C에서 중단."},
              "limit_verified": False, "command": command}
    receipt = output / "execution.json"
    save(receipt, record)
    try:
        record["before"] = admit(output)
        record["status"] = "running"; save(receipt, record)
        wrapped = ["timeout", "--signal=TERM", "--kill-after=5s", "600s", "prlimit", "--as=4294967296", "--core=0", "--", *command]
        with (output / "console.log").open("x") as console, (output / "thermal.jsonl").open("x") as monitor, (output / "threads.jsonl").open("x") as tids:
            process = subprocess.Popen(wrapped, cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                       stdout=console, stderr=subprocess.STDOUT, start_new_session=True)
            reason = None
            try:
                while process.poll() is None:
                    row = sample(); monitor.write(json.dumps(row) + "\n"); monitor.flush()
                    reason = rejection(row, record["before"]["history_limits"])
                    if reason:
                        record.update(rejection=reason, rejected_sample=row); terminate(process); break
                    if not record["limit_verified"]:
                        candidates = list(output.glob("evaluations/eval-*/manifest.json")) + list(output.glob("worker.json"))
                        for path in candidates:
                            try:
                                info = json.loads(path.read_text()); pid = info["process_id"]
                                limits = Path(f"/proc/{pid}/limits").read_text()
                                if not re.search(r"Max address space\s+4294967296\s+4294967296\s+bytes", limits):
                                    raise RuntimeError("address_space_limit_mismatch")
                                record.update(limit_verified=True, process_id=pid, limits_text=limits); save(receipt, record)
                            except (FileNotFoundError, KeyError, json.JSONDecodeError): pass
                    if record["limit_verified"]:
                        try:
                            snapshot = thread_snapshot(record["process_id"])
                            snapshot["clock_ticks_per_second"] = os.sysconf("SC_CLK_TCK")
                            tids.write(json.dumps(snapshot) + "\n"); tids.flush()
                        except (OSError, ValueError, IndexError) as error:
                            tids.write(json.dumps({"error": repr(error)}) + "\n"); tids.flush()
                    time.sleep(1)
                record["exit_code"] = process.wait()
            finally:
                terminate(process)
        after = sample()
        reason = reason or rejection(after, record["before"]["history_limits"])
        record["after"] = after
        record["status"] = ("control_rejected" if reason else "completed" if record["exit_code"] == 0 and record["limit_verified"] else "failed_or_unverified")
        record["control_valid"] = record["status"] == "completed"
        if reason: record["rejection"] = reason
    except (Exception, KeyboardInterrupt) as error:
        record.update(status="blocked", error=f"{type(error).__name__}: {error}")
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat(); save(receipt, record)
    print(json.dumps({"output": str(output), "status": record["status"], "rejection": record.get("rejection"), "error": record.get("error")}, ensure_ascii=False), flush=True)
    return 0 if record["control_valid"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(); command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command: parser.error("실행 명령이 필요합니다.")
    if subprocess.check_output(["git", "status", "--porcelain"]).strip(): parser.error("소스 변경을 먼저 commit/push/pull 하세요.")
    raise SystemExit(run(Path.cwd(), args.output.resolve(), command))
