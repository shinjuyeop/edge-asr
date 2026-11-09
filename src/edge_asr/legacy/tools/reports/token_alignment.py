"""연속 Zip native 토큰·실제 수신·VAD 구조 진단의 모델 없는 재검증."""

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json
from edge_asr.legacy.token_alignment import AlignmentAudit, inspect_snapshot
from edge_asr.legacy.tools.reports.concurrent import summarize as summarize_asr
from edge_asr.metrics import distribution


def summarize(folder, output):
    with tempfile.TemporaryDirectory() as directory:
        base = summarize_asr(folder, Path(directory) / "asr.json")
    worker = json.loads((folder / "worker.json").read_text())
    track = json.loads((folder / "track.json").read_text())
    if (
        not worker["settings"].get("token_diagnostics")
        or worker["settings"].get("replacements_enabled") is not False
    ):
        raise ValueError("연속 Zip의 교체 없는 토큰 진단 모드가 필요합니다.")
    sources = {s["id"]: s for s in track["sources"]}
    snapshots = read_jsonl(folder / "zip-token-snapshots.jsonl")
    events = read_jsonl(folder / "token-alignment-events.jsonl")
    jobs = read_jsonl(folder / "refine-jobs.jsonl")
    results = read_jsonl(folder / "refine-results.jsonl")
    receipts = read_jsonl(folder / "refine-receipts.jsonl")
    zlog = read_jsonl(folder / "zip-events.jsonl")
    expected = []
    for row in zlog:
        if row["type"] == "partial":
            expected.append(
                (
                    track["sources"][row["source_index"]]["id"],
                    "partial",
                    row["end"],
                    row["text"],
                    row["at_ns"],
                )
            )
        elif row["type"] == "source_final":
            expected.append(
                (row["id"], "final", row["end"], row["hypothesis"], row["finalized_ns"])
            )
    if len(snapshots) != len(expected):
        raise ValueError("Zip 반환/진단 스냅샷 수 불일치")
    checks = []
    previous = {}
    for n, (row, want) in enumerate(zip(snapshots, expected)):
        if (
            row["index"] != n
            or tuple(row[k] for k in ("source_id", "phase", "end", "text", "returned_ns")) != want
        ):
            raise ValueError("Zip 실제 반환과 토큰 스냅샷 식별 불일치")
        source = sources[row["source_id"]]
        if (
            row["source_start"] != source["start"]
            or not source["start"] < row["end"] <= source["end"]
            or row["snapshot_read_ns"] < row["returned_ns"]
        ):
            raise ValueError("스냅샷 오디오/시계 좌표 오류")
        if row["tail_padding_s"] != worker["zip_config"]["model"]["tail_padding_s"]:
            raise ValueError("모델 tail 설정과 스냅샷 불일치")
        checks.append(inspect_snapshot(row, previous.get(source["id"])))
        previous[source["id"]] = row
    rmap = {r["index"]: r for r in results}
    jmap = {j["index"]: j for j in jobs}
    recv = {}
    for receipt in receipts:
        i = receipt["index"]
        original = rmap[i]
        if i in recv or any(
            receipt[k] != original[k]
            for k in ("source_id", "start", "end", "payload_float32_sha256", "status", "ended_ns")
        ):
            raise ValueError("실제 Refine 반환/수신 불일치")
        if not original["ended_ns"] <= receipt["received_ns"] <= receipt["recorded_ns"]:
            raise ValueError("Refine 수신 시계 오류")
        recv[i] = receipt
    audit = AlignmentAudit(track["sources"])
    first_candidate = {}
    changes = []
    seen_snapshots = []
    last_at = -1
    for row in events:
        event = dict(row["event"])
        kind = event["type"]
        if not last_at <= row["at_ns"] <= row["assessed_ns"]:
            raise ValueError("구조 진단 시계 역행")
        last_at = row["at_ns"]
        if kind == "snapshot":
            original = snapshots[event.pop("snapshot_index")]
            if (
                event["source_id"] != original["source_id"]
                or row["at_ns"] < original["snapshot_read_ns"]
            ):
                raise ValueError("진단 snapshot 참조/시계 오류")
            seen_snapshots.append(original["index"])
            event["snapshot"] = original
        elif kind == "job":
            original = jmap[event["job"]["index"]]
            if any(
                event["job"][k] != original[k]
                for k in ("index", "source_id", "start", "end", "payload_float32_sha256")
            ) or event["job"]["accepted"] != (original["type"] == "enqueue"):
                raise ValueError("실제 큐와 진단 작업 불일치")
        elif kind == "receipt":
            original = recv[event["result"]["index"]]
            if (
                any(event["result"][k] != original[k] for k in event["result"])
                or row["at_ns"] < original["received_ns"]
            ):
                raise ValueError("진단의 실제 수신 참조 불일치")
        actual = audit.apply(event)
        if actual != row["changes"]:
            raise ValueError("구조 진단 재생 결과 불일치")
        for change in actual:
            if change["replacement_performed"] is not False:
                raise ValueError("교체 없는 진단이어야 합니다.")
            change = {**change, "assessed_ns": row["assessed_ns"]}
            changes.append(change)
            if change["state"] == "structural_candidate":
                first_candidate.setdefault(change["index"], change)
    if seen_snapshots != list(range(len(snapshots))):
        raise ValueError("진단 스냅샷 적용 누락/중복")
    complete = bool(
        base["input_complete"]
        and base["refine"]["complete"]
        and len(receipts) == len(results)
        and set(audit.jobs) == set(jmap)
        and set(audit.received) == set(rmap)
    )
    if worker["status"] == "completed" and not complete:
        raise ValueError("완료 실행의 토큰/수신 진단이 불완전합니다.")
    valid_first = [
        x
        for i, x in first_candidate.items()
        if audit.last[i]["state"] == "structural_candidate"
        and all(
            x[k] == audit.last[i][k] for k in ("token_range", "character_range", "zip_span_text")
        )
    ]
    ended = worker["replay"].get("t0_monotonic_ns")
    before_eof = (
        sum(
            x["assessed_ns"] < ended + sources[x["source_id"]]["end"] / 16000 * 1e9
            for x in first_candidate.values()
        )
        if ended
        else None
    )
    final_states = Counter(x["state"] for x in audit.last.values())
    final_reasons = Counter(reason for x in audit.last.values() for reason in x["reasons"])
    diagnostics = {
        "complete": complete,
        "snapshots": len(snapshots),
        "valid_snapshots": sum(c["valid"] for c in checks),
        "snapshot_failure_reasons": dict(
            Counter(reason for c in checks for reason in c["reasons"])
        ),
        "prefix_revision_snapshots": sum(not c["prefix_unchanged"] for c in checks),
        "source_final_snapshots": sum(r["phase"] == "final" for r in snapshots),
        "snapshot_read_wall_s": distribution([r["snapshot_read_wall_s"] for r in snapshots]),
        "audit_processing_wall_s": distribution(
            [(r["assessed_ns"] - r["at_ns"]) / 1e9 for r in events]
        ),
        "received_results": len(receipts),
        "child_end_to_parent_receive_s": distribution(
            [(r["received_ns"] - r["ended_ns"]) / 1e9 for r in receipts]
        ),
        "jobs": len(jobs),
        "ever_structural_candidate_jobs": len(first_candidate),
        "first_candidate_before_source_audio_end": before_eof,
        "final_states": dict(final_states),
        "final_hold_reasons": dict(final_reasons),
        "candidate_remains_same_in_final_snapshot": len(valid_first),
        "candidate_changed_or_revoked_later": len(first_candidate) - len(valid_first),
        "child_end_to_first_structural_candidate_s": distribution(
            [(x["assessed_ns"] - recv[i]["ended_ns"]) / 1e9 for i, x in first_candidate.items()]
        ),
        "actual_replacements": 0,
        "candidate_merged_cer": None,
        "actual_merge_apply_s": None,
        "acoustic_alignment_validated": False,
        "token_durations": None,
        "scope_ko": "연속 Zip의 native 디코더 시각/원문 재구성/접두부/word marker 기준 구조 후보 진단. 단어/음소 정답 경계·교체 안전성·UI 검증이 아니며 실제 전사는 교체하지 않음.",
    }
    base.update(schema="continuous-token-diagnostic-summary-v1", token_diagnostics=diagnostics)
    for name in (
        "zip-token-snapshots.jsonl",
        "token-alignment-events.jsonl",
        "refine-receipts.jsonl",
    ):
        base["source_sha256"][name] = digest(folder / name)
    write_json(output, base)
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.run, args.output)


if __name__ == "__main__":
    main()
