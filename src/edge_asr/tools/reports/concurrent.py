"""동시 실행 원시의 큐/시간축/완료율/CER를 모델 없이 재검증한다."""

import argparse
import math
import re
from pathlib import Path

from edge_asr.artifacts import digest, write_json
from edge_asr.metrics import character_errors, distribution
from edge_asr.streaming import backlog_series, read_events, validate_telemetry

RATE, WINDOW = 16000, 80000


def corpus(sources, hypotheses, complete):
    valid = [s for s in sources if isinstance(hypotheses.get(s["id"]), str)]
    scores = [character_errors(s["reference"], hypotheses[s["id"]], "ko_cer_v1") for s in valid]
    nonempty = [s for s in scores if s["reference_characters"] > 0]
    chars = sum(s["reference_characters"] for s in nonempty)
    edits = {
        k: sum(s[k] for s in nonempty)
        for k in ("errors", "substitutions", "deletions", "insertions")
    }
    result = {
        "planned": len(sources),
        "successful": len(valid),
        "complete": complete and len(valid) == len(sources),
        "cer": {
            **edits,
            "reference_characters": chars,
            "value": edits["errors"] / chars
            if complete and len(valid) == len(sources) and chars
            else None,
        },
        "scope": "null CER when capture/required output is incomplete; raw successful prefix preserved",
    }
    # 현재 KSS/합성 입력에는 빈 정답이 없다. 재사용 시 공통 scorer의 무음 정책을 유지한다.
    empty = [s for s in scores if s["reference_characters"] == 0]
    if empty:
        result["cer"].update(
            empty_reference_samples=len(empty),
            empty_reference_nonempty_hypotheses=sum(
                bool(s["normalized_hypothesis"]) for s in empty
            ),
        )
    return result


def resource_summary(rows, t0, end, model_roles):
    selected = [r for r in rows if t0 <= r["at_ns"] <= end]
    cpu = wall = 0.0
    peaks = []
    paired = 0
    for row in selected:
        values = [row["roles"].get(role, {}).get("sample") for role in model_roles]
        if all(v is not None for v in values):
            peaks.append(sum(v["rss_mib"] for v in values))
    for a, b in zip(selected, selected[1:]):
        dt = (b["at_ns"] - a["at_ns"]) / 1e9
        deltas = []
        for role in model_roles:
            x, y = a["roles"].get(role), b["roles"].get(role)
            if not x or not y or x["sample"] is None or y["sample"] is None or x["pid"] != y["pid"]:
                break
            delta = y["sample"]["cpu_s"] - x["sample"]["cpu_s"]
            if delta < 0 or dt <= 0:
                raise ValueError("프로세스 CPU/시계 역행")
            deltas.append(delta)
        if len(deltas) == len(model_roles):
            cpu += sum(deltas)
            wall += dt
            paired += 1
    return {
        "model_roles": model_roles,
        "sample_count": len(selected),
        "paired_intervals": paired,
        "paired_wall_s": wall,
        "observed_model_cpu_percent": 100 * cpu / wall if wall else None,
        "model_group_rss_observed_max_mib": max(peaks, default=None),
        "complete_model_rss_samples": len(peaks),
        "scope": "같은 인접 표본에서 모델 PID들이 모두 관측된 구간의 CPU 합/시간. 1코어=100%. producer 제외. RSS 합은 같은 시각 표본이며 공유 페이지 중복 가능.",
    }


def thread_summary(rows):
    result = {}
    for role in ("zip", "refine"):
        snapshots = [
            r["roles"][role] | {"ticks_per_s": r["clock_ticks_per_second"]}
            for r in rows
            if role in r["roles"] and "threads" in r["roles"][role]
        ]
        totals = {}
        for a, b in zip(snapshots, snapshots[1:]):
            if a["process_start_ticks"] != b["process_start_ticks"]:
                raise ValueError("모델 PID 재사용")
            dt = b["at_monotonic_s"] - a["at_monotonic_s"]
            for tid, new in b["threads"].items():
                old = a["threads"].get(tid)
                if not old or old["start_ticks"] != new["start_ticks"]:
                    continue
                dc = (new["cpu_ticks"] - old["cpu_ticks"]) / b["ticks_per_s"]
                if dc < 0 or dt <= 0:
                    raise ValueError("TID CPU/시계 역행")
                item = totals.setdefault(
                    (tid, new["start_ticks"]), {"tid": tid, "cpu_s": 0.0, "paired_s": 0.0}
                )
                item["cpu_s"] += dc
                item["paired_s"] += dt
        for item in totals.values():
            item["mean_cpu_percent"] = 100 * item["cpu_s"] / item["paired_s"]
        result[role] = {
            "snapshots": len(snapshots),
            "observed_tid_max": max((len(r["threads"]) for r in snapshots), default=None),
            "active_at_least_1_percent": sum(r["mean_cpu_percent"] >= 1 for r in totals.values())
            if snapshots
            else None,
            "rows": sorted(totals.values(), key=lambda r: r["cpu_s"], reverse=True),
            "runtime_roles_confirmed": False,
        }
    return result


def summarize(folder, output):
    if output.exists():
        raise FileExistsError(output)
    import json

    worker = json.loads((folder / "worker.json").read_text())
    track = json.loads((folder / "track.json").read_text())
    sources = track["sources"]
    replay = worker.get("replay", {})
    t0 = replay.get("t0_monotonic_ns")
    jobs = read_events(folder / "refine-jobs.jsonl")
    results = read_events(folder / "refine-results.jsonl")
    zrows = read_events(folder / "zip-sources.jsonl")
    zlog = read_events(folder / "zip-events.jsonl")
    refine = worker["refine_threads"] != 0
    policy = worker.get("settings", {}).get("refine_policy", "fixed5")
    is_vad = policy != "fixed5"
    vad_ready = True
    expected = []
    position = 0
    for i, s in enumerate(sources):
        if s["start"] != position or s["end"] <= s["start"]:
            raise ValueError("원음 시간축 오류")
        for start in range(s["start"], s["end"], WINDOW):
            expected.append((i, s["id"], start, min(start + WINDOW, s["end"])))
        position = s["end"]
    if position != track["frames"]:
        raise ValueError("트랙 길이 오류")
    if is_vad:
        from edge_asr.tools.reports.online_vad import validate_sources

        expected, vad_ready = validate_sources(folder, track, jobs, policy)
    for i, job in enumerate(jobs):
        if job["type"] not in (("enqueue", "drop") if refine else ("control_only",)):
            raise ValueError("실행 조건과 큐 이벤트 종류 불일치")
        if (
            i >= len(expected)
            or (job["source_index"], job["source_id"], job["start"], job["end"]) != expected[i]
        ):
            raise ValueError("Refine 구간 좌표 오류")
        if (
            job["index"] != i
            or job["payload_frames"]
            != job["end"] - (job["context_start"] if is_vad else job["start"])
            or not re.fullmatch("[a-f0-9]{64}", job["payload_float32_sha256"])
        ):
            raise ValueError("Refine payload 메타데이터 오류")
        if job["at_ns"] < job["submitted_ns"]:
            raise ValueError("큐 시각 역행")
    enqueued = {j["index"]: j for j in jobs if j["type"] == "enqueue"}
    seen = set()
    accepted_order = list(enqueued)
    for ordinal, row in enumerate(results):
        index = row["index"]
        if ordinal >= len(accepted_order) or index != accepted_order[ordinal]:
            raise ValueError("Refine FIFO 순서 오류")
        if index in seen or index not in enqueued:
            raise ValueError("중복/미제출 Refine 결과")
        job = enqueued[index]
        seen.add(index)
        if any(
            row[k] != job[k]
            for k in (
                "start",
                "end",
                "source_id",
                "payload_frames",
                "payload_float32_sha256",
                "submitted_ns",
            )
        ):
            raise ValueError("Refine IPC 결과 불일치")
        if is_vad and any(
            row[k] != job[k]
            for k in (
                "context_start",
                "context_end",
                "emitted_ns",
                "delivered_through_frame",
                "segment",
            )
        ):
            raise ValueError("Refine VAD 결과 메타데이터 불일치")
        if not job["at_ns"] <= row["dequeued_ns"] <= row["started_ns"] <= row["ended_ns"]:
            raise ValueError("Refine 시간축 오류")
    input_complete = bool(
        replay.get("complete")
        and replay.get("dropped_chunks") == 0
        and len(zrows) == len(sources)
        and all(r["status"] == "success" for r in zrows)
    )
    if replay.get("complete"):
        validate_telemetry(
            replay,
            {
                "elapsed_s": replay["processing_s"],
                "process_cpu_s": replay["processing_cpu_s"],
                "audio": {"duration_s": track["frames"] / RATE},
            },
        )
    zip_hyp = {}
    first = []
    final = []
    for i, row in enumerate(zrows):
        if i >= len(sources) or any(
            row[k] != sources[i][k] for k in ("id", "start", "end", "reference", "audio_sha256")
        ):
            raise ValueError("Zipformer 원본/순서 오류")
        if row["status"] == "success":
            zip_hyp[row["id"]] = row["hypothesis"]
        if input_complete:
            if row["first_partial_ns"] is not None:
                first.append((row["first_partial_ns"] - t0) / 1e9 - row["start"] / RATE)
            final.append((row["finalized_ns"] - t0) / 1e9 - row["end"] / RATE)
    summary = {
        "schema": "concurrent-wav-summary-v1",
        "status": worker["status"],
        "input_complete": input_complete,
        "zip_threads": worker["zip_threads"],
        "refine_threads": worker["refine_threads"],
        "manifest_sha256": track["manifest_sha256"],
        "track_float32_sha256": track["track_float32_sha256"],
        "audio_s": track["frames"] / RATE,
        "planned_sources": len(sources),
        "zip_cer": corpus(sources, zip_hyp, input_complete),
        "first_partial_from_source_start_s": distribution(first),
        "zip_finalize_from_source_end_s": distribution(final),
        "no_partial_sources": sum(r.get("first_partial_ns") is None for r in zrows)
        if input_complete
        else None,
        "input_dropped_chunks": replay.get("dropped_chunks"),
        "input_dropped_audio_s": replay.get("dropped_audio_s"),
        "input_backlog_event_max_audio_s": replay.get("backlog", {}).get("event_max_audio_s"),
        "input_backlog_audio_s": distribution(replay.get("backlog", {}).get("audio_s_values", [])),
        "input_queue_wait_s": distribution(replay.get("queue_wait_s_values", [])),
        "chunk_processing_s": distribution(replay.get("chunk_processing_s_values", [])),
        "coordinator_active_rtf": replay.get("processing_s", 0) / (track["frames"] / RATE)
        if input_complete
        else None,
        "refine": None,
        "refine_reason": "streaming_only_control" if not refine else None,
        "producer_cpu_s": replay.get("producer_cpu_s"),
        "producer_lifetime_peak_rss_mib": replay.get("producer_lifetime_peak_rss_mib"),
        "unmeasured": worker["unmeasured"],
    }
    calls = [r for r in zlog if r["type"] == "zip_call"]
    if input_complete:
        summary["zip_call_rtf"] = (
            sum((r["ended_ns"] - r["started_ns"]) / 1e9 for r in calls) / summary["audio_s"]
        )
    else:
        summary["zip_call_rtf"] = None
    end = worker.get("measurement_finished_ns")
    if t0 and end:
        summary["resources"] = resource_summary(
            read_events(folder / "process-resources.jsonl"),
            t0,
            end,
            ["zip", "refine"] if refine else ["zip"],
        )
        summary["threads"] = thread_summary(read_events(folder / "thread-resources.jsonl"))
        if is_vad:
            summary["resources"]["main_pid_components"] = worker["main_pid_components"]
    else:
        summary.update(resources=None, threads=None)
    if refine and t0:
        complete = (
            input_complete
            and vad_ready
            and len(jobs) == len(expected)
            and len(results) == len(expected)
            and len(enqueued) == len(expected)
            and all(r["status"] == "success" for r in results)
        )
        if worker["status"] == "completed" and not complete:
            raise ValueError("완료 실행의 Refine 결과가 불완전합니다.")
        valid = [r for r in results if r["status"] == "success"]
        hypotheses = (
            {
                s["id"]: " ".join(r["hypothesis"] for r in valid if r["source_id"] == s["id"])
                for s in sources
            }
            if complete
            else {}
        )
        queue_wait = [(r["dequeued_ns"] - enqueued[r["index"]]["at_ns"]) / 1e9 for r in valid]
        decode = [(r["ended_ns"] - r["started_ns"]) / 1e9 for r in valid]
        returned = [(r["ended_ns"] - t0) / 1e9 - r["end"] / RATE for r in valid]
        dispatch = [(enqueued[r["index"]]["at_ns"] - t0) / 1e9 - r["end"] / RATE for r in valid]
        file_final = (
            [
                (max(r["ended_ns"] for r in valid if r["source_id"] == s["id"]) - t0) / 1e9
                - s["end"] / RATE
                for s in sources
                if any(r["source_id"] == s["id"] for r in valid)
            ]
            if complete
            else []
        )
        elapsed = ((end or max([t0, *[r["ended_ns"] for r in valid]])) - t0) / 1e9
        produced = [
            {
                "type": j["type"],
                "index": j["index"],
                "start": j["start"],
                "end": j["end"],
                "at_s": (j["at_ns"] - t0) / 1e9,
            }
            for j in jobs
            if j["type"] in ("enqueue", "drop")
        ]
        consumed = [{"index": r["index"], "at_s": (r["dequeued_ns"] - t0) / 1e9} for r in results]
        _, backlog = backlog_series(produced, consumed, RATE, elapsed, summary["audio_s"])
        if backlog["event_max_chunks"] > 2:
            raise ValueError("Refine 큐 용량 초과")
        overlap = 0.0
        overlapping_jobs = 0
        for row in valid:
            shared = sum(
                max(
                    0, min(row["ended_ns"], c["ended_ns"]) - max(row["started_ns"], c["started_ns"])
                )
                for c in calls
            )
            overlap += shared / 1e9
            overlapping_jobs += shared > 0
        summary["refine"] = {
            "complete": complete,
            "planned_windows": len(expected),
            "enqueued": len(enqueued),
            "successful": len(valid),
            "failed_windows": sum(r["status"] != "success" for r in results),
            "missing_results": len(enqueued) - len(results),
            "dropped_windows": sum(j["type"] == "drop" for j in jobs),
            "cer": corpus(sources, hypotheses, complete),
            "queue_wait_s": distribution(queue_wait),
            "decode_s": distribution(decode),
            "dispatch_from_window_end_s": distribution(dispatch),
            "window_end_to_return_s": distribution(returned),
            "final_from_source_file_end_s": distribution(file_final),
            "backlog_event_max_audio_s": backlog["event_max_audio_s"],
            "backlog_event_max_windows": backlog["event_max_chunks"],
            "backlog_audio_s": distribution(backlog["audio_s_values"]),
            "backlog_end_audio_s": backlog["end_audio_s"],
            "zip_refine_call_overlap_s": overlap,
            "refine_calls_with_zip_overlap": overlapping_jobs,
            "overlap_scope": "native 호출 wall 구간 겹침. 같은 순간의 CPU 실행 시간을 직접 입증하는 값은 아님.",
        }
        if is_vad:
            emitted = [(r["emitted_ns"] - t0) / 1e9 - r["end"] / RATE for r in valid]
            dispatch_after = [
                (enqueued[r["index"]]["at_ns"] - r["emitted_ns"]) / 1e9 for r in valid
            ]
            emission_to_return = [(r["ended_ns"] - r["emitted_ns"]) / 1e9 for r in valid]
            decision_audio = [
                (r["segment"]["emitted_at_audio_frame"] - r["segment"]["owned_end_frame"]) / RATE
                for r in valid
            ]
            payload_produced = [
                {**event, "start": jobs[event["index"]]["context_start"]} for event in produced
            ]
            _, payload_backlog = backlog_series(
                payload_produced, consumed, RATE, elapsed, summary["audio_s"]
            )
            summary["refine_policy"] = policy
            summary["refine"].update(
                vad_emission_from_owned_end_s=distribution(emitted),
                dispatch_after_vad_emission_s=distribution(dispatch_after),
                vad_emission_to_return_s=distribution(emission_to_return),
                decision_audio_after_owned_end_s=distribution(decision_audio),
                no_vad_segment_sources=sum(
                    not any(j["source_id"] == s["id"] for j in jobs) for s in sources
                )
                if vad_ready
                else None,
                backlog_event_max_payload_audio_s=payload_backlog["event_max_audio_s"],
                final_from_source_file_end_scope="구간이 있는 원본만. 파일 끝 대비 마지막 인식 반환의 부호 있는 차이이며 음수 가능. 음향 끝/UI 확정 지연 아님.",
            )
            if any(
                not math.isfinite(v) or v < 0
                for values in (emitted, dispatch_after, emission_to_return, decision_audio)
                for v in values
            ):
                raise ValueError("VAD 비유한/음수 지연")
    observations = [first, final]
    if refine and t0:
        observations += [queue_wait, decode, returned, dispatch]
        if not is_vad:
            observations.append(file_final)
        elif any(not math.isfinite(v) for v in file_final):
            raise ValueError("비유한 파일 끝 상대 시각")
    if any(not math.isfinite(value) or value < 0 for values in observations for value in values):
        raise ValueError("비유한/음수 지연")
    owned_names = {
        "worker.json",
        "track.json",
        "refine-jobs.jsonl",
        "refine-results.jsonl",
        "zip-sources.jsonl",
        "zip-events.jsonl",
        "process-resources.jsonl",
        "thread-resources.jsonl",
        "vad-sources.jsonl",
    }
    summary["source_sha256"] = {
        str(p.relative_to(folder)): digest(p)
        for p in folder.rglob("*")
        if p.is_file() and (p.name in owned_names or p.parent.name == "capture")
    }
    if "refine_family" in worker:
        summary["refine_family"] = worker["refine_family"]
    write_json(output, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.run, args.output)


if __name__ == "__main__":
    main()
