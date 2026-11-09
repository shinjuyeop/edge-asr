"""세 모델의 같은 KSS·1/2/4스레드 단회 탐색 비교. 각 실행에 운영 열 정책을 적용한다."""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.adapters.qwen3 import context_budget
from edge_asr.artifacts import digest, write_json
from edge_asr.config import load_evaluation_config
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.tools.probes.thermal import run

CONFIGS = {
    "zipformer": "configs/legacy/zipformer_eval.toml",
    "sensevoice": "configs/legacy/sensevoice_eval.toml",
    "qwen3_asr": "configs/legacy/qwen3_eval.toml",
}


def plan_cases():
    # 순서를 결과 확인 전에 고정한다. 각 모델/스레드별 반복은 1회로 명시한다.
    orders = (
        (1, ("qwen3_asr", "zipformer", "sensevoice")),
        (2, ("sensevoice", "qwen3_asr", "zipformer")),
        (4, ("zipformer", "sensevoice", "qwen3_asr")),
    )
    return [
        {"id": f"{family}-t{threads}-01", "family": family, "num_threads": threads}
        for threads, families in orders
        for family in families
    ]


def campaign(root, output, manifest):
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip():
        raise RuntimeError("먼저 Host commit/push 후 RP5의 clean main을 동기화하세요.")
    output.mkdir(parents=True, exist_ok=False)
    (output / "configs").mkdir()
    qwen = load_evaluation_config(root / CONFIGS["qwen3_asr"])["model"]
    if (qwen["max_total_len"], qwen["max_new_tokens"]) != (512, 128):
        raise ValueError("이번 탐색 계획의 Qwen token 예산은 512/128입니다.")
    samples, manifest_hash = load_manifest(manifest, root)
    inputs = []
    for sample in samples:
        audio = load_audio(sample.audio_path, 16000)
        if sample.audio_sha256 != audio.sha256:
            raise ValueError(f"WAV 해시 불일치: {sample.id}")
        budget = context_budget(len(audio.samples), 128, 512)
        if not budget["fits"]:
            raise ValueError(f"Qwen context 예산 초과: {sample.id}")
        inputs.append({"id": sample.id, "audio": audio.metadata(), "qwen_context_budget": budget})
    cases = plan_cases()
    for case in cases:
        source = root / CONFIGS[case["family"]]
        config = output / "configs" / (case["id"] + ".toml")
        text, count = re.subn(
            r"(?m)^num_threads = \d+$", f"num_threads = {case['num_threads']}", source.read_text()
        )
        if count != 1:
            raise ValueError("num_threads 설정을 한 곳에서만 바꿀 수 있어야 합니다.")
        config.write_text(text)
        checked = load_evaluation_config(config)
        if (
            checked["evaluation"]["warmup_runs"] != 3
            or checked["evaluation"]["normalization"] != "ko_cer_v1"
        ):
            raise ValueError("warmup=3·ko_cer_v1 조건이 필요합니다.")
        case.update(config=str(config), config_sha256=digest(config))
    plan = {
        "schema_version": 1,
        "scope": "exploratory_single_pass_batch_operational_thermal_policy",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "python": sys.executable,
        "manifest": str(manifest),
        "manifest_sha256": manifest_hash,
        "samples": len(samples),
        "inputs": inputs,
        "normalization": "ko_cer_v1",
        "warmup_runs": 3,
        "repetitions_per_configuration": 1,
        "temperature_policy": "operational",
        "selection_policy": None,
        "selection_policy_reason": "단회 탐색 결과. 최적 스레드/반복 안정성/실시간 채택을 판정하지 않음",
        "cases": cases,
    }
    write_json(output / "plan.json", plan)
    results = {"status": "running", "cases": []}
    code = 0
    try:
        for case in cases:
            command = [
                sys.executable,
                "-u",
                "scripts/edge_asr.py",
                "evaluate",
                "--config",
                case["config"],
                "--manifest",
                str(manifest),
                "--output-dir",
                str(output / case["id"] / "evaluations"),
            ]
            status = run(root, output / case["id"], command, temperature_policy="operational")
            results["cases"].append({"id": case["id"], "exit_code": status})
            write_json(output / "campaign.json", results)
            if status:
                code = status
                break
        results["status"] = "completed" if code == 0 else "stopped_after_failure"
    except BaseException as error:
        results.update(status="failed", error=repr(error))
        code = 1
        raise
    finally:
        results["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "campaign.json", results)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/evaluation/kss-dev50/manifest.jsonl")
    )
    args = parser.parse_args()
    return campaign(Path.cwd(), args.output.resolve(), args.manifest.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
