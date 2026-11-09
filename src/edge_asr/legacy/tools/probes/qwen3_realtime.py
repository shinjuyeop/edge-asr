"""같은 VAD 구간의 Offline 비교 후 Zip+Refine 1배속 동시 실행을 검증한다."""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.tools.probes.thermal import run

CONFIGS = {
    "sensevoice": "configs/legacy/sensevoice_eval.toml",
    "qwen3_asr": "configs/legacy/qwen3_eval.toml",
}
DATASETS = {
    "kss": "data/evaluation/kss-dev50/manifest.jsonl",
    "synthetic120": "data/evaluation/m23-stress/long-120.jsonl",
}
VARIANTS = (("sensevoice", 2), ("qwen3_asr", 1), ("qwen3_asr", 2))


def plan_cases():
    cases = []
    for phase in ("offline", "concurrent"):
        for dataset in DATASETS:
            variants = VARIANTS if dataset == "kss" else tuple(reversed(VARIANTS))
            for family, threads in variants:
                cases.append(
                    {
                        "id": f"{phase}-{dataset}-{family}-t{threads}-01",
                        "phase": phase,
                        "dataset": dataset,
                        "family": family,
                        "num_threads": threads,
                        "repeat": 1,
                    }
                )
    # 2T 재확인은 결과를 보기 전에 정한다. 다른 구성의 반복 안정성을 주장하지 않는다.
    cases.append(
        {
            "id": "concurrent-kss-qwen3_asr-t2-02",
            "phase": "concurrent",
            "dataset": "kss",
            "family": "qwen3_asr",
            "num_threads": 2,
            "repeat": 2,
        }
    )
    return cases


def verify_offline_geometry(output, cases):
    identities, counts = {}, {}
    keys = (
        "owned_start_frame",
        "owned_end_frame",
        "context_start_frame",
        "context_end_frame",
        "payload_sha256",
        "payload_frames",
        "reason",
    )
    for case in cases:
        if case["phase"] != "offline":
            continue
        rows = read_jsonl(output / case["id"] / "predictions.jsonl")
        if not rows or any(r["status"] != "success" for r in rows):
            raise ValueError("모든 Offline 파일이 성공해야 온라인 단계로 진행합니다.")
        identity = [
            (
                r["id"],
                r["reference"],
                r["audio"]["sha256"],
                [tuple(s[k] for k in keys) for s in r["segmented"]["segments"]],
            )
            for r in rows
        ]
        dataset = case["dataset"]
        if dataset in identities and identities[dataset] != identity:
            raise ValueError("모델/스레드 사이의 VAD 소유 구간·정답·PCM이 다릅니다.")
        identities[dataset] = identity
        counts[dataset] = {"sources": len(rows), "segments": sum(len(r[3]) for r in identity)}
    return {"same_segments_references_and_pcm": True, "datasets": counts}


def queue_loss_only(directory):
    """계측/추론 오류 없이 큐 포화만 관측한 경우에만 다음 고정 조건을 계속한다."""
    try:
        receipt = json.loads((directory / "execution.json").read_text())
        worker = json.loads((directory / "worker.json").read_text())
        summary = json.loads((directory / "summary.json").read_text())
        refine = summary["refine"]
        return (
            receipt["status"] == "failed_or_unverified"
            and receipt["exit_code"] == 1
            and receipt["limit_verified"]
            and not receipt.get("rejection")
            and not receipt.get("error")
            and worker["status"] == "measurement_with_losses"
            and not worker.get("resource_sampling_error")
            and not worker.get("summary_error")
            and worker.get("refine_alive") is False
            and worker.get("refine_exit_code") == 0
            and summary["input_complete"]
            and refine["dropped_windows"] > 0
            and refine["failed_windows"] == 0
            and refine["missing_results"] == 0
            and refine["successful"] == refine["enqueued"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def campaign(root, output):
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip():
        raise RuntimeError("Host commit/push 후 RP5의 clean main에서 실행하세요.")
    output.mkdir(parents=True, exist_ok=False)
    (output / "configs").mkdir()
    inputs = {}
    for name, path in DATASETS.items():
        samples, sha = load_manifest(root / path, root)
        rows = []
        for sample in samples:
            audio = load_audio(sample.audio_path, 16000)
            if sample.audio_sha256 != audio.sha256:
                raise ValueError(f"WAV 해시 불일치: {sample.id}")
            rows.append({"id": sample.id, "audio": audio.metadata()})
        inputs[name] = {"manifest": path, "manifest_sha256": sha, "inputs": rows}
    cases = plan_cases()
    for case in cases:
        source = root / CONFIGS[case["family"]]
        config = output / "configs" / (case["id"] + ".toml")
        text, count = re.subn(
            r"(?m)^num_threads = \d+$", f"num_threads = {case['num_threads']}", source.read_text()
        )
        if count != 1:
            raise ValueError("스레드 설정은 한 곳에 있어야 합니다.")
        config.write_text(text)
        checked = load_evaluation_config(config)
        if (
            checked["evaluation"]["normalization"] != "ko_cer_v1"
            or checked["evaluation"]["warmup_runs"] != 3
        ):
            raise ValueError("ko_cer_v1·워밍업 3회가 필요합니다.")
        if case["family"] == "qwen3_asr" and (
            checked["model"]["max_total_len"],
            checked["model"]["max_new_tokens"],
        ) != (512, 128):
            raise ValueError("Qwen 입력/출력 예산 512/128을 유지합니다.")
        case.update(config=str(config), config_sha256=digest(config))
    plan = {
        "schema": "qwen3-vad-concurrent-plan-v1",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.executable,
        "datasets": inputs,
        "cases": cases,
        "normalization": "ko_cer_v1",
        "settings": {
            "policy": "vad0",
            "maximum_owned_s": 5,
            "context_frames": 0,
            "preprocessing": "identity",
            "warmup_runs": 3,
            "warmup_first_window_s": 5,
            "zip_threads": 1,
            "vad_threads": 1,
            "refine_queue_capacity": 2,
            "thermal_policy": "operational",
            "automatic_replacements": 0,
        },
        "scope": "KSS/합성 개발 자료. 구성별1회, Qwen2T KSS 동시 실행만 사전 지정 2회. 마이크/독립 평가/장시간 안정성 아님",
        "failure_policy": "추론/입력/계측/외부 제한 실패는 중단. 검증된 Refine 큐 포화만 있으면 실패를 보존하고 다음 고정 조건 계속",
    }
    write_json(output / "plan.json", plan)
    results = {"status": "running", "cases": []}
    code, loss_seen, offline_verified = 0, False, False
    try:
        for case in cases:
            if case["phase"] == "concurrent" and not offline_verified:
                write_json(output / "offline-geometry.json", verify_offline_geometry(output, cases))
                offline_verified = True
            directory = output / case["id"]
            command = [sys.executable, "-u", "scripts/edge_asr.py", "tools"]
            if case["phase"] == "offline":
                command += [
                    "probe-segmented-accuracy",
                    "--config",
                    case["config"],
                    "--condition",
                    "bounded_vad_5s",
                    "--warmup-first-window",
                ]
            else:
                command += [
                    "probe-concurrent",
                    "--zip-threads",
                    "1",
                    "--refine-threads",
                    str(case["num_threads"]),
                    "--refine-policy",
                    "vad0",
                    "--refine-config",
                    case["config"],
                ]
            command += [
                "--manifest",
                str(root / DATASETS[case["dataset"]]),
                "--output",
                str(directory),
            ]
            status = run(root, directory, command, temperature_policy="operational")
            loss = bool(status and case["phase"] == "concurrent" and queue_loss_only(directory))
            results["cases"].append(
                {"id": case["id"], "exit_code": status, "queue_loss_only": loss}
            )
            loss_seen |= loss
            write_json(output / "campaign.json", results)
            if status and not loss:
                code = status
                break
        results["status"] = (
            "stopped_after_failure"
            if code
            else ("completed_with_losses" if loss_seen else "completed")
        )
    except BaseException as error:
        code = 1
        results.update(status="failed", error=repr(error))
        raise
    finally:
        results["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "campaign.json", results)
    return code or int(loss_seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return campaign(Path.cwd(), args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
