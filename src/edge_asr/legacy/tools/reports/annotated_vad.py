"""RP5 원시 VAD 구간을 Host에서 재채점하고 동일 성공 표본의 예비 비교를 집계한다."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import read_jsonl
from edge_asr.metrics import distribution
from edge_asr.thermal import rejection
from edge_asr.vad import ANNOTATED_SETTINGS as SETTINGS
from edge_asr.vad import MODEL_SHA
from edge_asr.vad_annotations import load_annotations, rates, score_segments, select_development


def aggregate(rows):
    counts = Counter()
    for row in rows:
        counts.update(row["score"]["counts"])
    # 빈 집합의 값도 검출 실패 0건이라는 뜻으로 사용하지 않는다.
    if not rows:
        return {
            "successful": 0,
            "frame_counts": None,
            "speech_miss_fraction": None,
            "non_speech_false_alarm_fraction": None,
            "pipeline_rtf": None,
            "mean_process_cpu_percent": None,
            "rss_observed_max_mib": None,
        }
    wall = sum(r["call_wall_s"] for r in rows)
    audio = sum(r["frames"] / 16000 for r in rows)
    targets = [
        r["score"]["target_fully_missed"]
        for r in rows
        if r["score"]["target_fully_missed"] is not None
    ]
    rss = [
        r["resources"]["rss_observed_max_mib"]
        for r in rows
        if r["resources"]["rss_observed_max_mib"] is not None
    ]
    return {
        "successful": len(rows),
        "frame_counts": dict(counts),
        **rates(counts),
        "target_eligible": len(targets),
        "target_fully_missed": sum(targets) if targets else None,
        "clips_with_emissions": sum(bool(r["segments"]) for r in rows),
        "audio_s": audio,
        "call_wall_s": wall,
        "pipeline_rtf": wall / audio,
        "mean_process_cpu_percent": 100 * sum(r["process_cpu_s"] for r in rows) / wall,
        "rss_observed_max_mib": max(rss, default=None),
        "endpoint_audio_timeline_s": distribution(
            [
                r["score"]["endpoint"]["audio_timeline_delay_s"]
                for r in rows
                if r["score"]["endpoint"]["audio_timeline_delay_s"] is not None
            ]
        ),
    }


def summarize(root, annotations, runs):
    refs, sha = load_annotations(annotations, root)
    policies = {}
    sources = set()
    modes = set()
    for policy in ("identity", "rms26"):
        folder = runs / policy
        info = json.loads((folder / "worker.json").read_text())
        if info.get("backend", "native") != "native":
            raise ValueError("실험용 확률 VAD는 기존 네이티브 비교표에 합치지 않습니다.")
        control = json.loads((folder / "execution.json").read_text())
        selected = select_development(refs, info["mode"])
        expected = {r["id"]: r for r in selected}
        if (
            info["annotation_sha256"] != sha
            or info["policy"] != policy
            or info["settings"] != SETTINGS
            or info["model_sha256"] != MODEL_SHA
            or info["planned_ids"] != list(expected)
            or info["status"] not in ("completed", "completed_with_failures")
        ):
            raise ValueError("실행 설정·입력·완료 기록 불일치")
        if (
            not control.get("control_valid")
            or not control.get("limit_verified")
            or control["status"] != "completed"
        ):
            raise ValueError("열/외부 제한을 확인하지 못한 실행은 비교 표에 합치지 않습니다.")
        thermal = read_jsonl(folder / "thermal.jsonl")
        history = control["before"]["history_limits"]
        if not thermal or any(
            rejection(r, history) for r in [control["before"], *thermal, control["after"]]
        ):
            raise ValueError("온도·현재 제한·계측 결측")
        rows = read_jsonl(folder / "samples.jsonl")
        if (
            len(rows) != len(expected)
            or len({r["id"] for r in rows}) != len(rows)
            or {r["id"] for r in rows} != set(expected)
        ):
            raise ValueError("누락/중복/보류 표본의 실행 기록")
        warm = json.loads((folder / "warmup.json").read_text())
        if (
            warm["status"] != "completed"
            or warm["id"]
            != next((r for r in selected if r["category"] == "short"), selected[0])["id"]
        ):
            raise ValueError("워밍업 기록 오류")
        for row in [warm, *rows]:
            ref = expected[row["id"]]
            if (
                row["audio_sha256"] != ref["audio_sha256"]
                or row["frames"] != ref["frames"]
                or row["category"] != ref["category"]
                or row["annotation_status"] != ref["annotation_status"]
            ):
                raise ValueError("실행 음원/주석 메타데이터 불일치")
            for key in ("call_wall_s", "process_cpu_s"):
                value = row[key]
                if (
                    type(value) not in (float, int)
                    or not math.isfinite(value)
                    or value < 0
                    or (key == "call_wall_s" and value == 0)
                ):
                    raise ValueError("잘못된 자원 계측")
            if row["status"] == "completed":
                m = row["pipeline"]
                want = ref["expected_processed"][policy]
                if (
                    not m["complete"]
                    or m["frames"] != ref["frames"]
                    or m["processed_pcm_sha256"] != want["pcm_sha256"]
                    or m["newly_clipped_samples"] != want["newly_clipped_samples"]
                    or m["native_budget_exhausted"]
                ):
                    raise ValueError("공통 PCM/실행 완료 불일치")
                if score_segments(ref, row["segments"]) != row["score"]:
                    raise ValueError("Host 재채점 불일치")
            elif row["status"] != "failed" or row["score"] is not None or not row["error"]:
                raise ValueError("실패를 성공/정답 결과로 기록할 수 없습니다.")
        failures = [{"id": r["id"], "error": r["error"]} for r in rows if r["status"] == "failed"]
        if len(failures) != info["failed_samples"] or (info["status"] == "completed") != (
            not failures
        ):
            raise ValueError("실패 수 불일치")
        policies[policy] = {
            "rows": rows,
            "failures": failures,
            "loading": info["loading"],
            "warmup_call_s": warm["call_wall_s"],
            "max_temperature_c": max(r["temperature_mc"] for r in thermal) / 1000,
            "thermal_samples": len(thermal),
            "runtime": info["runtime"],
        }
        sources.add(control["source_commit"])
        modes.add(info["mode"])
    if len(sources) != 1 or len(modes) != 1:
        raise ValueError("서로 다른 소스/주석 모드 비교")
    paired = set.intersection(
        *({r["id"] for r in p["rows"] if r["status"] == "completed"} for p in policies.values())
    )
    categories = sorted({r["category"] for r in selected})
    table = {}
    for category in categories:
        table[category] = {
            policy: aggregate(
                [r for r in data["rows"] if r["category"] == category and r["id"] in paired]
            )
            for policy, data in policies.items()
        }
    return {
        "schema": "annotated-vad-comparison-v1",
        "source_commit": sources.pop(),
        "mode": modes.pop(),
        "annotation_sha256": sha,
        "planned_per_policy": len(selected),
        "paired_successful": len(paired),
        "holdout_not_executed": sum(r["split"] == "holdout" for r in refs),
        "table": table,
        "policies": {k: {n: v for n, v in d.items() if n != "rows"} for k, d in policies.items()},
        "scope": "배포 주석 예비 비교와 사람 검수 정답 분리. 표는 양쪽 성공한 같은 표본만 집계. 실패는 policies에 보존.",
        "gold_accuracy_available": all(
            r["annotation_status"] == "human_reviewed" for r in selected
        ),
        "asr_cer": None,
        "asr_rtf": None,
        "streaming_first_text_s": None,
        "refine_delay_s": None,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--annotations", type=Path, required=True)
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = summarize(a.root.resolve(), a.annotations, a.runs)
    with a.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(
        json.dumps(
            {
                "paired_successful": result["paired_successful"],
                "mode": result["mode"],
                "output": str(a.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
