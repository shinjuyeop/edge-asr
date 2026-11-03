"""구간별 Zip/Refine 실제 수신·병합 로그의 모델 없는 재검증과 채점."""

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json
from edge_asr.merging import POLICIES, Merger
from edge_asr.metrics import character_errors, distribution, normalize
from edge_asr.tools.reports.concurrent import corpus
from edge_asr.tools.reports.concurrent import summarize as summarize_asr
from edge_asr.tools.reports.merge_replay import suffix_change


def summarize(folder, output):
    with tempfile.TemporaryDirectory() as directory:
        base = summarize_asr(folder, Path(directory) / "asr.json")
    worker = json.loads((folder / "worker.json").read_text())
    if not worker["settings"].get("segment_merge") or worker["settings"]["refine_policy"] != "vad0":
        raise ValueError("문맥 없는 구간 병합 모드가 필요합니다.")
    track = json.loads((folder / "track.json").read_text())
    jobs = read_jsonl(folder / "refine-jobs.jsonl")
    segments = read_jsonl(folder / "zip-segments.jsonl")
    receipts = read_jsonl(folder / "refine-receipts.jsonl")
    results = read_jsonl(folder / "refine-results.jsonl")
    rows = read_jsonl(folder / "merge-events.jsonl")
    decisions = json.loads((folder / "merge-decisions.json").read_text())
    smap = {s["segment_key"]: s for s in segments}
    jmap = {j["segment_key"]: j for j in jobs}
    model_returns = {r["segment_key"]: r for r in results}
    pmap = {
        (r["segment_key"], r["at_ns"]): r
        for r in read_jsonl(folder / "zip-events.jsonl")
        if r["type"] == "partial"
    }
    engines = {p: Merger(worker["merge_session_id"], p) for p in POLICIES}
    final_updates = {p: {} for p in POLICIES}
    for row in rows:
        event = row["event"]
        engine = engines[row["policy"]]
        if event["session_id"] != worker["merge_session_id"]:
            raise ValueError("병합 세션 불일치")
        if row["applied_ns"] < event["at_ns"]:
            raise ValueError("병합 적용 시계 역행")
        returned, received = row.get("returned_ns"), row.get("received_ns")
        if returned is not None and returned > event["at_ns"]:
            raise ValueError("모델 반환보다 이른 적용")
        if received is not None and not returned <= received <= event["at_ns"]:
            raise ValueError("실제 parent 수신 시계 오류")
        key, kind = event.get("source_id"), event["type"]
        if kind == "partial":
            original = pmap[(key, returned)]
            if any(event[k] != original[k] for k in ("end", "text")):
                raise ValueError("Zip partial과 병합 입력 불일치")
        elif kind == "close":
            original = smap[key]
            if (
                event["text"] != original["hypothesis"]
                or returned != original["finalized_ns"]
                or event["end"] != original["end"]
            ):
                raise ValueError("Zip 최종과 병합 입력 불일치")
        elif kind in ("job", "refine"):
            original = jmap[key]
            if (
                event["job_id"] != original["index"]
                or any(
                    event[k] != original[k]
                    for k in ("start", "end", "context_start", "context_end")
                )
                or event["payload_sha256"] != original["payload_float32_sha256"]
            ):
                raise ValueError("실제 작업과 병합 입력 불일치")
            if kind == "job" and event["accepted"] != (original["type"] == "enqueue"):
                raise ValueError("큐 승인과 병합 승인 불일치")
            if kind == "refine":
                native = model_returns[key]
                if (
                    event["text"] != native.get("hypothesis")
                    or event["status"] != native["status"]
                    or returned != native["ended_ns"]
                ):
                    raise ValueError("Refine 실제 반환과 병합 입력 불일치")
        before = len(engine.updates)
        if event["type"] == "register":
            engine.register(event["source_id"], event["start"], event["end"], end_is_bound=True)
        elif event["type"] == "finish":
            engine.finish(event["at_ns"])
        else:
            if engine.apply(event) != row["acceptance"]:
                raise ValueError("병합 이벤트 승인 결과 불일치")
        if engine.updates[before:] != row["updates"]:
            raise ValueError("병합 상태 재생이 실제 적용과 다릅니다.")
        for update in row["updates"]:
            if update["phase"] == "final":
                final_updates[row["policy"]][update["source_id"]] = {
                    **update,
                    "applied_ns": row["applied_ns"],
                }
    for policy, engine in engines.items():
        actual = {
            "finals": [s.decision for s in engine.sources.values()],
            "ignored_events": dict(engine.ignored),
        }
        if actual != decisions["policies"][policy]:
            raise ValueError("병합 최종 스냅샷 불일치")
    if len({r["segment_key"] for r in segments}) != len(segments):
        raise ValueError("Zip 구간 ID 중복")
    by_index = {j["index"]: j for j in jobs}
    rmap = {r["index"]: r for r in results}
    seen = set()
    for segment in segments:
        job = by_index[segment["index"]]
        if any(
            segment[k] != job[k]
            for k in (
                "segment_key",
                "source_id",
                "start",
                "end",
                "payload_frames",
                "payload_float32_sha256",
            )
        ):
            raise ValueError("두 모델 구간 ID/실제 PCM 해시 불일치")
        if segment["payload_frames"] != segment["end"] - segment["start"]:
            raise ValueError("구간 입력 길이 불일치")
        first = segment["first_partial_ns"]
        chunks = [r for (key, _), r in pmap.items() if key == segment["segment_key"]]
        cursor = segment["start"]
        for chunk in chunks:
            if chunk["input_start"] != cursor or not cursor < chunk["end"] <= min(
                segment["end"], cursor + 1600, chunk["delivered_through_frame"]
            ):
                raise ValueError("Zip 실제 청크 누락/중복/미래 입력")
            if chunk["feeding_phase"] == "active_prefix":
                if chunk["end"] > chunk["delivered_through_frame"] - 4800:
                    raise ValueError("Zip 안전 보류 범위 위반")
            elif chunk["feeding_phase"] != "emission_catchup":
                raise ValueError("잘못된 Zip 입력 단계")
            cursor = chunk["end"]
        observed_first = next((r["at_ns"] for r in chunks if r["text"]), None)
        if cursor != segment["end"] or first != observed_first:
            raise ValueError("Zip 완료 입력/첫 부분 계측 불일치")
        if segment["first_partial_before_emission"] != (
            first is not None and first < segment["emitted_ns"]
        ):
            raise ValueError("방출 전 첫 부분 계측 불일치")
    for receipt in receipts:
        index = receipt["index"]
        original = rmap[index]
        if (
            index in seen
            or receipt["segment_key"] != original["segment_key"]
            or receipt["ended_ns"] != original["ended_ns"]
        ):
            raise ValueError("중복/잘못된 실제 수신")
        if not original["ended_ns"] <= receipt["received_ns"] <= receipt["recorded_ns"]:
            raise ValueError("수신 시계 역행")
        seen.add(index)
    complete = bool(
        base["input_complete"]
        and base["refine"]["complete"]
        and decisions["drained"]
        and len(segments) == len(jobs)
        and len(receipts) == len(results)
        and all(s.decision is not None for e in engines.values() for s in e.sources.values())
    )
    if worker["status"] == "completed" and not complete:
        raise ValueError("완료 실행의 구간/수신/병합이 불완전합니다.")
    if complete and any(set(e.sources) != set(smap) for e in engines.values()):
        raise ValueError("실제 구간과 병합 구간 집합 불일치")
    policies = {}
    zip_hyp = {
        s["id"]: " ".join(x["hypothesis"] for x in segments if x["source_id"] == s["id"])
        for s in track["sources"]
    }
    receipt_by_key = {r["segment_key"]: r for r in receipts}
    t0 = worker["replay"].get("t0_monotonic_ns")
    for policy, engine in engines.items():
        finals = [s.decision for s in engine.sources.values() if s.decision is not None]
        chosen = {
            s["id"]: " ".join(
                x["text"]
                for x in finals
                if smap.get(x["source_id"], {}).get("source_id") == s["id"]
            )
            for s in track["sources"]
        }
        deltas = [
            character_errors(s["reference"], chosen[s["id"]], "ko_cer_v1")["errors"]
            - character_errors(s["reference"], zip_hyp[s["id"]], "ko_cer_v1")["errors"]
            for s in track["sources"]
            if normalize(s["reference"])
        ]
        changed = [u for u in engine.updates if u["text_changed"]]
        applied = list(final_updates[policy].values()) if complete else []
        policies[policy] = {
            "cer": corpus(track["sources"], chosen, complete),
            "selection_counts": dict(Counter(x["model"] for x in finals)),
            "decision_reasons": dict(Counter(x["reason"] for x in finals)),
            "ignored_events": dict(engine.ignored),
            "versus_segmented_zip": {
                "improved_sources": sum(d < 0 for d in deltas),
                "tied_sources": sum(d == 0 for d in deltas),
                "worsened_sources": sum(d > 0 for d in deltas),
            }
            if complete
            else None,
            "visible_text_changes": len(changed),
            "normalized_suffix_rewrite_events": sum(
                suffix_change(u["previous_text"], u["text"])[0] > 0 for u in changed
            ),
            "normalized_removed_suffix_characters": sum(
                suffix_change(u["previous_text"], u["text"])[0] for u in changed
            ),
            "owned_end_to_final_apply_s": distribution(
                [(x["applied_ns"] - t0) / 1e9 - x["end"] / 16000 for x in applied]
            ),
            "vad_emit_to_final_apply_s": distribution(
                [(x["applied_ns"] - smap[x["source_id"]]["emitted_ns"]) / 1e9 for x in applied]
            ),
            "zip_final_to_final_apply_s": distribution(
                [(x["applied_ns"] - smap[x["source_id"]]["finalized_ns"]) / 1e9 for x in applied]
            ),
            "received_to_event_apply_s": distribution(
                [
                    (row["applied_ns"] - row["received_ns"]) / 1e9
                    for row in rows
                    if row["policy"] == policy and row.get("received_ns") is not None
                ]
            ),
            "first_refine_ready_to_segment_final_s": distribution(
                [
                    (x["applied_ns"] - receipt_by_key[x["source_id"]]["ended_ns"]) / 1e9
                    for x in applied
                    if x["model"] == "refine"
                ]
            ),
            "finals_before_original_file_end": sum(
                x["applied_ns"]
                < t0
                + next(
                    s["end"]
                    for s in track["sources"]
                    if s["id"] == smap[x["source_id"]]["source_id"]
                )
                / 16000
                * 1e9
                for x in applied
            ),
        }
    base.update(
        schema="segment-merge-wav-summary-v1",
        segment_merge={
            "complete": complete,
            "zip_state_policy": "reset_per_vad_owned_segment",
            "zip_holdback_frames": 4800,
            "zip_chunk_frames": 1600,
            "same_actual_pcm_segments": len(segments),
            "received_results": len(receipts),
            "first_partial_before_vad_emission": sum(
                s["first_partial_before_emission"] for s in segments
            ),
            "no_partial_segments": sum(s["first_partial_ns"] is None for s in segments),
            "first_partial_from_owned_start_s": distribution(
                [
                    (s["first_partial_ns"] - t0) / 1e9 - s["start"] / 16000
                    for s in segments
                    if s["first_partial_ns"] is not None
                ]
            )
            if complete
            else distribution([]),
            "child_end_to_parent_receive_s": distribution(
                [(r["received_ns"] - r["ended_ns"]) / 1e9 for r in receipts]
            ),
            "policies": policies,
            "scope_ko": "실제1배속 WAV/VAD 구간별 Zip 상태 초기화·동일 PCM의 Refine·parent 수신·메모리 상태 적용. UI/마이크/음향 정답 경계·단어 정렬 미측정. 보류와 reset으로 이전 연속 Zip과 다른 실험 조건이다.",
        },
    )
    for name in (
        "zip-segments.jsonl",
        "merge-events.jsonl",
        "merge-decisions.json",
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
