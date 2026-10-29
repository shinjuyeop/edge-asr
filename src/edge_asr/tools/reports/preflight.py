"""M3-0 통제 기록 재채점과 사전 고정 공통 정책 선택. 원시 결과는 읽기만 한다."""

import argparse
import json
import statistics
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.scoring import read_predictions, score_records


def select_policy(comparisons, clipping):
    """사전 고정된 2모델 × 3조건 전체가 유효할 때만 한 정책을 고른다."""
    families = ("zipformer", "sensevoice")
    policies = ("identity", "gain12", "rms26")
    conditions = ("clean", "quiet24", "quiet24_noise55")
    if set(comparisons) != set(policies):
        raise ValueError("세 정책의 전체 결과가 필요합니다.")
    for policy in policies:
        if set(comparisons[policy]) != set(families):
            raise ValueError("두 모델의 결과가 필요합니다.")
        for family in families:
            groups = comparisons[policy][family]
            if set(groups) != set(conditions):
                raise ValueError("세 조건이 필요합니다.")
            if any(
                not g["complete"] or g["successful"] != 50 or g["cer"]["value"] is None
                for g in groups.values()
            ):
                raise ValueError("모든 조건의 50개 성공 결과가 필요합니다.")
    eligible, audit = [], {}
    for policy in policies:
        regressions = {
            family: comparisons[policy][family]["clean"]["cer"]["value"]
            - comparisons["identity"][family]["clean"]["cer"]["value"]
            for family in families
        }
        mean = statistics.mean(
            comparisons[policy][family][c]["cer"]["value"]
            for family in families
            for c in conditions
        )
        good = clipping[policy] == 0 and all(v <= 0.01 + 1e-12 for v in regressions.values())
        audit[policy] = {
            "eligible": good,
            "newly_clipped_samples": clipping[policy],
            "clean_cer_regression": regressions,
            "mean_development_cer": mean,
        }
        if good:
            eligible.append(policy)
    selected = min(eligible, key=lambda p: (audit[p]["mean_development_cer"], policies.index(p)))
    return {
        "selected_common_policy": selected,
        "candidates": audit,
        "scope": "KSS 합성 음량 개발 규칙. VAD·실제 잡음·연속 상태·독립 최종 평가 합격을 뜻하지 않음.",
    }


def summarize(base, data):
    comparisons, evidence = {}, []
    for family in ("zipformer", "sensevoice"):
        directory = base / f"load-{family}-t1"
        receipt = json.loads((directory / "execution.json").read_text())
        worker = json.loads((directory / "worker.json").read_text())
        if not receipt["control_valid"] or worker["status"] != "completed":
            raise ValueError("연속 부하 통제 조건이 충족되지 않았습니다.")
    reference_maps, model_maps = {}, {}
    for directory in sorted(base.glob("volume-*")):
        receipt = json.loads((directory / "execution.json").read_text())
        if not receipt["control_valid"]:
            raise ValueError("통제 실패 실행은 정책 선택에 사용할 수 없습니다.")
        manifests = list(directory.glob("evaluations/eval-*/manifest.json"))
        if len(manifests) != 1:
            raise ValueError("단일 평가 기록이 필요합니다.")
        path = manifests[0]
        info = json.loads(path.read_text())
        family = info["config"]["model"]["family"]
        policy = directory.name.split("-")[2]
        if info["warmup"]["successful"] != 3 or info["config"]["model"]["num_threads"] != 1:
            raise ValueError("워밍업/스레드 조건 불일치")
        rows = read_predictions(path.parent / "predictions.jsonl")
        expected = {
            r["id"]: r
            for r in (
                json.loads(line) for line in (data / f"dev-{policy}.jsonl").read_text().splitlines()
            )
        }
        if {r["id"] for r in rows} != set(expected):
            raise ValueError("평가 ID 불일치")
        for r in rows:
            e = expected[r["id"]]
            if (r["reference"], r["audio"]["sha256"]) != (e["reference"], e["audio_sha256"]):
                raise ValueError("입력/정답 불일치")
        mapping = {r["id"]: r["reference"] for r in rows}
        if reference_maps and next(iter(reference_maps.values())) != mapping:
            raise ValueError("정책 사이 정답 불일치")
        reference_maps[policy] = mapping
        hashes = {k: v["sha256"] for k, v in info["model_files"].items()}
        if family in model_maps and model_maps[family] != hashes:
            raise ValueError("가중치 불일치")
        model_maps[family] = hashes
        summary, _ = score_records(rows, "ko_cer_v1")
        saved = json.loads((path.parent / "summary.json").read_text())
        if summary != saved:
            raise ValueError("Host 재채점 불일치")
        if family in comparisons.get(policy, {}):
            raise ValueError("중복 모델/정책 실행")
        comparisons.setdefault(policy, {})[family] = summary["by_condition"]
        evidence.append(
            {
                "run": directory.name,
                "manifest_sha256": digest(path),
                "predictions_sha256": digest(path.parent / "predictions.jsonl"),
                "rescore_equal": True,
            }
        )
    stats = json.loads((data / "audio-stats.json").read_text())
    clipping = {
        p: sum(r["newly_clipped_samples"] for r in stats if r["policy"] == p)
        for p in ("identity", "gain12", "rms26")
    }
    return {
        "schema_version": 1,
        "comparisons": comparisons,
        "selection": select_policy(comparisons, clipping),
        "evidence": evidence,
        "unmeasured": {
            "streaming_first_partial_s": None,
            "refine_queue_s": None,
            "endpoint_delay_s": None,
            "real_acoustic_recognition_latency_s": None,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data/evaluation/m30-volume-dev"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = summarize(args.base, args.data)
    with args.output.open("x", encoding="utf-8") as out:
        out.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(value["selection"], ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
