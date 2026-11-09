"""회수한 M2-3 기록을 Host에서 재채점·검증하고 작은 JSON 요약을 생성한다."""

import argparse
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.scoring import read_predictions, score_records


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(campaign):
    records, groups, models, sources, runtimes, pairs = [], defaultdict(list), {}, {}, {}, {}
    model_policies, environment_identity = {}, None
    for receipt in sorted(campaign.glob("*/*/execution.json")):
        execution = read(receipt)
        phase, slot = receipt.parent.parent.name, receipt.parent.name
        runs = list((receipt.parent / "evaluations").glob("eval-*"))
        run = runs[0] if len(runs) == 1 else None
        row = {
            "phase": phase,
            "slot": slot,
            "family": execution["family"],
            "num_threads": execution["num_threads"],
            "status": execution["status"],
            "exit_code": execution.get("exit_code"),
            "diagnostic_only": execution["diagnostic_only"],
            "limit_verified": execution["limit_verified"],
            "cooldown_s": execution.get("cooldown_s"),
            "run_id": run.name if run else None,
            "overall": None,
            "host_rescore_matches": None,
        }
        samples = (
            [
                json.loads(line)
                for line in (receipt.parent / "device.jsonl").read_text().splitlines()
            ]
            if (receipt.parent / "device.jsonl").exists()
            else []
        )
        temperatures = [
            s["temperature_millicelsius"] / 1000
            for s in samples
            if s["temperature_millicelsius"] is not None
        ]
        frequencies = [s["cpu0_freq_khz"] for s in samples if s["cpu0_freq_khz"] is not None]
        row["device"] = {
            "before": execution.get("before"),
            "after": execution.get("after"),
            "temperature_observed_max_c": max(temperatures, default=None),
            "sample_count": len(samples),
            "cpu0_freq_observed_min_khz": min(frequencies, default=None),
            "cpu0_freq_observed_max_khz": max(frequencies, default=None),
        }
        before_temp = (execution.get("before") or {}).get("temperature_millicelsius")
        row["launch_snapshot_above_cooldown_threshold"] = (
            before_temp > 55000 if before_temp is not None else None
        )
        if run and (run / "manifest.json").exists():
            info = read(run / "manifest.json")
            environment = read(run / "environment.json")
            identity = {k: environment[k] for k in ("python", "machine", "platform", "runtime")}
            if environment_identity is not None and identity != environment_identity:
                raise ValueError("실험 중 Python/장치/런타임 정보가 다릅니다.")
            environment_identity = identity
            key = row["family"]
            model = {
                name: {"name": Path(v["path"]).name, "sha256": v["sha256"]}
                for name, v in info["model_files"].items()
            }
            if key in models and models[key] != model:
                raise ValueError("실험 중 모델 바이트가 다릅니다.")
            models[key] = model
            policy = info["model_metadata"]["adapter"]
            if key in model_policies and model_policies[key] != policy:
                raise ValueError("실험 중 모델 출력/언어 정책이 다릅니다.")
            model_policies[key] = policy
            sources[info["source_commit"]] = info["source_sha256"]
            runtimes[info["source_commit"]] = environment["runtime"]
            row.update(
                source_commit=info["source_commit"],
                source_dirty=info["source_dirty"],
                manifest_sha256=info["input_manifest"]["sha256"],
                warmup=info["warmup"],
                model_load_s=info["model_load_s"],
                model_load_cpu_s=info["model_load_cpu_s"],
                model_load_rss_mib=(info.get("model_load_resources") or {}).get(
                    "rss_observed_max_mib"
                ),
            )
        if run and (run / "summary.json").exists():
            predictions = read_predictions(run / "predictions.jsonl")
            summary, scores = score_records(predictions, "ko_cer_v1")
            if summary != read(run / "summary.json"):
                raise ValueError(f"Host 재채점 불일치: {run}")
            row.update(overall=summary["overall"], host_rescore_matches=True)
            row["cpu_seconds_per_audio_second"] = (
                sum(p["process_cpu_s"] for p in predictions if p["status"] == "success")
                / summary["overall"]["total_audio_s"]
                if summary["overall"]["total_audio_s"]
                else None
            )
            if phase == "matrix":
                groups[(row["family"], row["num_threads"])].append(row)
            if phase == "external":
                pairs[row["family"]] = (predictions, scores)
                row["by_reference_digit_presence"] = {
                    label: score_records(
                        [
                            p
                            for p in predictions
                            if bool(re.search(r"[0-9]", p["reference"])) == present
                        ],
                        "ko_cer_v1",
                    )[0]["overall"]
                    for label, present in (("ascii_digit", True), ("no_ascii_digit", False))
                    if any(
                        bool(re.search(r"[0-9]", p["reference"])) == present for p in predictions
                    )
                }
            if phase == "silence":
                row["silence_outputs"] = [
                    {
                        "id": p["id"],
                        "status": p["status"],
                        "hypothesis": p["hypothesis"],
                        "duration_s": (p.get("audio") or {}).get("duration_s"),
                        "adapter_result": p.get("adapter_result"),
                    }
                    for p in predictions
                ]
                row["normalized_output_characters"] = sum(
                    s.get("hypothesis_characters", len(s.get("normalized_hypothesis", "")))
                    for s in scores
                )
                row["raw_text_nonempty_count"] = sum(bool(p.get("hypothesis")) for p in predictions)
        if (receipt.parent / "threads.json").exists():
            observation = read(receipt.parent / "threads.json")
            row["thread_diagnostic"] = {
                k: observation[k]
                for k in (
                    "status",
                    "observed_wall_s",
                    "thread_count_observed_max",
                    "threads",
                    "runtime_thread_roles_reason",
                )
            }
        row["execution_sha256"] = digest(receipt)
        records.append(row)
    if set(groups) != {
        (family, threads) for family in ("zipformer", "sensevoice") for threads in (1, 2, 4)
    }:
        raise ValueError("전체 6개 행렬 구성이 필요합니다.")
    aggregated = []
    for (family, threads), rows in sorted(groups.items()):
        valid = [r for r in rows if r["status"] == "completed" and r["overall"]["complete"]]
        if len(valid) != 3:
            raise ValueError("3회 전체 완료되지 않은 행렬 구성")
        rtf = [r["overall"]["rtf"] for r in valid]
        if len({r["overall"]["cer"]["value"] for r in valid}) != 1:
            raise ValueError("반복 간 CER 변화")
        aggregated.append(
            {
                "family": family,
                "num_threads": threads,
                "completed_runs": len(valid),
                "cer": valid[0]["overall"]["cer"],
                "median_rtf": statistics.median(rtf),
                "rtf_range": [min(rtf), max(rtf)],
                "median_cpu_percent": statistics.median(
                    r["overall"]["mean_process_cpu_percent"] for r in valid
                ),
                "median_cpu_seconds_per_audio_second": statistics.median(
                    r["cpu_seconds_per_audio_second"] for r in valid
                ),
                "rss_observed_max_mib": max(r["overall"]["rss_observed_max_mib"] for r in valid),
                "median_load_s": statistics.median(r["model_load_s"] for r in valid),
                "load_rss_observed_max_mib": max(r["model_load_rss_mib"] for r in valid),
                "temperature_observed_max_c": max(
                    r["device"]["temperature_observed_max_c"] for r in valid
                ),
            }
        )
    paired = None
    if set(pairs) == {"zipformer", "sensevoice"}:
        z, s = pairs["zipformer"], pairs["sensevoice"]
        signature = lambda rows: [
            (
                p["id"],
                p["reference"],
                p["audio"]["sha256"] if p.get("audio") else p["expected_audio_sha256"],
            )
            for p in rows
        ]
        if signature(z[0]) != signature(s[0]):
            raise ValueError("외부 자료의 모델 간 입력 불일치")
        successful = [(a, b) for a, b in zip(z[1], s[1]) if a["status"] == b["status"] == "success"]
        paired = {
            "both_successful": len(successful),
            "sensevoice_fewer_errors": sum(b["errors"] < a["errors"] for a, b in successful),
            "same_errors": sum(b["errors"] == a["errors"] for a, b in successful),
            "sensevoice_more_errors": sum(b["errors"] > a["errors"] for a, b in successful),
            "scope": "동일 발화의 오류 개수 비교. 실제 Hybrid 수정 성능이 아님.",
        }
    source_lists = list(sources.values())
    if source_lists and any(value != source_lists[0] for value in source_lists):
        raise ValueError("실험 단계 사이 공통 ASR 소스 변경")
    verification = {
        "host_rescore_matching_runs": sum(r["host_rescore_matches"] is True for r in records)
    }
    if (campaign / "preservation-after.json").exists():
        audit = read(campaign / "preservation-after.json")
        if (
            audit["differences"]
            or not audit["packages_unchanged"]
            or any(d["differences"] for d in audit["new_data"])
        ):
            raise ValueError("보존/데이터 검사 불일치")
        verification.update(
            protected_files=audit["protected_files"],
            protected_files_unchanged=True,
            packages_unchanged=True,
            new_data=audit["new_data"],
        )
    if (campaign / "artifact-checksums.json").exists():
        checksums = read(campaign / "artifact-checksums.json")
        if any(digest(campaign / name) != checksum for name, checksum in checksums.items()):
            raise ValueError("회수한 원시 기록의 체크섬 불일치")
        verification.update(
            remote_artifacts_verified=len(checksums),
            checksum_list_sha256=digest(campaign / "artifact-checksums.json"),
        )
    datasets = {}
    for name in ("fleurs-ko-test100", "m23-stress"):
        path = Path.cwd() / "data/evaluation" / name / "provenance.json"
        if path.exists():
            expected = next(
                (
                    item["provenance_sha256"]
                    for item in verification.get("new_data", [])
                    if item["dataset"] == name
                ),
                None,
            )
            if expected is not None and digest(path) != expected:
                raise ValueError("현재 로컬 데이터 출처가 RP5 전송/보존 기록과 다릅니다.")
            provenance = read(path)
            datasets[name] = {
                k: provenance[k]
                for k in (
                    "recipe",
                    "gender_counts",
                    "unique_sentence_ids",
                    "speaker_count",
                    "speaker_count_reason",
                    "training_overlap_verified",
                    "synthetic_long",
                    "short_durations_s",
                )
                if k in provenance
            }
            datasets[name]["provenance_sha256"] = digest(path)
    audio_validation = (
        read(campaign / "host-audio-validation.json")
        if (campaign / "host-audio-validation.json").exists()
        else None
    )
    if audio_validation:
        audio_validation = {k: v for k, v in audio_validation.items() if k != "files"}
    return {
        "schema_version": 1,
        "milestone": "M2-3",
        "summarized_at_utc": datetime.now(timezone.utc).isoformat(),
        "campaign": campaign.name,
        "normalization": "ko_cer_v1",
        "models": models,
        "model_policies": model_policies,
        "environment": environment_identity,
        "verification": verification,
        "datasets": datasets,
        "host_unit_tests": read(campaign / "host-unit-tests.json")
        if (campaign / "host-unit-tests.json").exists()
        else None,
        "host_audio_validation": audio_validation,
        "posthoc_level_analysis": read(campaign / "host-level-analysis.json")
        if (campaign / "host-level-analysis.json").exists()
        else None,
        "runtimes": runtimes,
        "source_commits": list(sources),
        "asr_source_identical_between_phases": True,
        "matrix": aggregated,
        "selection": read(campaign / "selection.json")
        if (campaign / "selection.json").exists()
        else None,
        "runs": records,
        "external_paired_errors": paired,
        "unmeasured": {
            key: None
            for key in (
                "streaming_first_partial_s",
                "offline_refine_confirmation_s",
                "endpoint_s",
                "hybrid_backlog",
                "hybrid_drops",
                "ui_display_s",
            )
        },
        "unmeasured_reason": "단독 batch 실행만 수행. 실시간 입력·VAD endpoint·동시 Refine·UI 없음.",
        "warmup_wall_s": None,
        "warmup_timing_reason": "기존 평가기는 워밍업 횟수·성공 수·사용 발화를 기록하며 워밍업 시간은 별도 계측하지 않음.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = summarize(args.campaign)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(
        f"재채점 확인한 실행 {sum(r['host_rescore_matches'] is True for r in value['runs'])}개: {args.output}"
    )


if __name__ == "__main__":
    main()
