"""저장된 Qwen 요청·발행 기록에서 의미 있는 첫 표시와 최신 입력 반영을 측정한다."""

import argparse
import json
import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json
from edge_asr.metrics import distribution, normalize
from edge_asr.qwen.viewer.analysis import visible
from edge_asr.qwen.viewer.boundary_errors import boundary_errors
from edge_asr.qwen.viewer.onsets import annotated_word_starts, onset_latencies


def letters(row):
    return "".join(c for c in normalize(row.get("hypothesis") or "") if c.isalnum())


def event_record(row):
    keys = (
        "job_id",
        "revision",
        "segment_id",
        "kind",
        "type",
        "token_index",
        "published_s",
        "start_frame",
        "owned_start_frame",
        "end_frame",
        "available_frame",
        "payload_float32_sha256",
        "hypothesis",
    )
    return {key: row[key] for key in keys if key in row}


def continuity(worker, records, *, word_starts=None):
    """원음은 겹치지 않는 연속 트랙 좌표다. 겹친 요청은 원음별 첫 표시에 배분하지 않는다."""
    sources = worker["sources"]
    ends = [s["end"] for s in sources]
    if any(
        s["start"] >= s["end"] or (i and s["start"] < ends[i - 1]) for i, s in enumerate(sources)
    ):
        raise ValueError("원음 구간이 겹치거나 시간 순서가 잘못되었습니다.")
    rate = worker["audio"]["sample_rate_hz"]
    duration = worker["audio"]["frames"] / rate
    t0 = worker.get("telemetry", {}).get("t0_monotonic_ns")
    t0 = None if t0 is None else t0 / 1e9
    rows = sorted(
        (e for e in records if e["type"] in ("result", "token")), key=lambda e: e["published_s"]
    )
    results = {e["job_id"]: e for e in rows if e["type"] == "result"}
    first_raw = {}
    decisions = defaultdict(list)
    for e in records:
        if e["type"] == "token":
            first_raw.setdefault(e["job_id"], e)
        if e["type"] == "partial_decision":
            decisions[e["segment_id"]].append(e)
    previous = {}
    first, four, old_first, owned_first = {}, {}, {}, {}
    final_at = {}
    fresh = []
    old_marks = []
    old_previous = {}
    old_end = -1
    furthest = -1
    for e in rows:
        lo, hi, at = e["start_frame"], e["end_frame"], e["published_s"]
        left = bisect_right(ends, lo)
        right = left
        while right < len(sources) and sources[right]["start"] < hi:
            right += 1
        if e.get("stale_revision"):
            continue
        owned_left = bisect_right(ends, e.get("owned_start_frame", lo))
        if e["type"] == "result" and e["kind"] == "final":
            # 이전 파일에서 빌린 왼쪽 문맥은 이 확정이 소유한 원음이 아니다.
            for index in range(owned_left, right):
                final_at[index] = at
        if not visible(e) or e.get("status") == "cancelled":
            continue
        unique = left + 1 == right
        if unique:
            old_first.setdefault(left, e)
        segment = e["segment_id"]
        if e["hypothesis"] != old_previous.get(segment):
            old_previous[segment] = e["hypothesis"]
            if hi > old_end:
                old_end = hi
                if at <= duration:
                    old_marks.append(at)
        text = letters(e)
        prior = previous.get(segment, "")
        # 순수 삭제·기존 접두부 재생은 새 글자가 아니다. 이때 워터마크를 전진시키지 않는다.
        # 같은 요청의 다음 토큰에서 새 내용이 생기면 그때 새 음성 범위를 인정한다.
        if not text or prior.startswith(text):
            continue
        previous[segment] = text
        if owned_left + 1 == right:
            owned_first.setdefault(owned_left, e)
        if unique:
            first.setdefault(left, e)
            if len(text) >= 4:
                four.setdefault(left, e)
        if hi > furthest:
            furthest = hi
            if at <= duration:
                fresh.append(e)

    def delays(events):
        return distribution(
            [v["published_s"] - sources[k]["start"] / rate for k, v in events.items()]
        )

    def explain(index):
        e = first[index]
        source = sources[index]
        start = source["start"] / rate
        job = e["job_id"]
        submit, begin = e.get("submitted_monotonic_s"), e.get("started_monotonic_s")
        offered = decisions.get(e["segment_id"], [])
        observed = offered[0]["at_monotonic_s"] if offered else None
        first_raw_at = first_raw.get(job, {}).get("published_s")
        native = (results.get(job, {}).get("adapter_result") or {}).get("native_result") or {}
        return {
            "source": source["id"],
            "source_start_s": start,
            "source_end_s": source["end"] / rate,
            "event": event_record(e),
            "latency_s": e["published_s"] - start,
            "source_to_first_logged_partial_decision_s": observed - t0 - start
            if observed is not None and t0 is not None
            else None,
            "first_decision_reason": offered[0]["reason"] if offered else None,
            "source_to_submit_s": submit - t0 - start
            if submit is not None and t0 is not None
            else None,
            "queue_s": begin - submit if begin is not None and submit is not None else None,
            "start_to_meaningful_s": e["published_s"] + t0 - begin
            if begin is not None and t0 is not None
            else None,
            "first_raw_token_published_s": first_raw_at,
            "raw_to_meaningful_s": e["published_s"] - first_raw_at
            if first_raw_at is not None
            else None,
            "completed_job_stages_s": {
                k: native[k]
                for k in (
                    "preprocess_s",
                    "audio_encode_s",
                    "audio_embedding_eval_s",
                    "prefix_eval_s",
                    "suffix_eval_s",
                    "token_decode_s",
                    "first_generated_token_s",
                )
                if k in native
            },
        }

    order = sorted(first, key=lambda i: first[i]["published_s"] - sources[i]["start"] / rate)
    ranks = (
        {str(p): explain(order[(p * len(order) + 99) // 100 - 1]) for p in (50, 95, 100)}
        if order
        else {}
    )
    times = [0.0, *(e["published_s"] for e in fresh), duration]
    gaps = [b - a for a, b in zip(times, times[1:])]
    old_bounds = [0.0, *old_marks, duration]
    old_gaps = [b - a for a, b in zip(old_bounds, old_bounds[1:])]
    worst = sorted(range(len(gaps)), key=lambda i: gaps[i], reverse=True)[:5]
    # 10 Hz 가상 입력 시계. 아직 최신 내용을 내지 못한 시작 구간은 미관측으로 집계한다.
    fresh_times = times[1:-1]
    ages = []
    samples_without = 0
    for tick in range(int(duration * 10) + 1):
        now = tick / 10
        i = bisect_right(fresh_times, now) - 1
        if i < 0:
            samples_without += 1
        else:
            ages.append(now - fresh[i]["end_frame"] / rate)
    timeline_windows = []
    for start in range(0, math.ceil(duration), 300):
        end = min(start + 300, duration)
        begin = bisect_left(fresh_times, start)
        stop = bisect_right(fresh_times, end) if end == duration else bisect_left(fresh_times, end)
        marks = [start, *fresh_times[begin:stop], end]
        # age 표본의 미관측 구간은 트랙 맨 앞에만 있다. 마지막 tick도 한 번만 집계한다.
        tick_start = max(0, start * 10 - samples_without)
        tick_stop = len(ages) if end == duration else max(0, int(end * 10) - samples_without)
        timeline_windows.append(
            {
                "start_s": start,
                "end_s": end,
                "fresh_displays": stop - begin,
                "max_gap_clipped_to_window_s": max(b - a for a, b in zip(marks, marks[1:])),
                "input_age_at_10hz_s": distribution(ages[tick_start:tick_stop]),
            }
        )
    windows = worker.get("segments", [])
    active_gaps = []
    for s in windows:
        a, b = s["owned_start_frame"] / rate, s["owned_end_frame"] / rate
        inner = fresh_times[bisect_left(fresh_times, a) : bisect_right(fresh_times, b)]
        bounds = [a, *inner, b]
        active_gaps.extend(y - x for x, y in zip(bounds, bounds[1:]))
    counts = Counter((r["kind"], r["status"]) for r in results.values())
    cancelled = [r for r in results.values() if r.get("status") == "cancelled"]
    partials = [r for r in results.values() if r["kind"] == "partial"]
    fresh_jobs = {e["job_id"] for e in fresh}
    without_fresh = [r for r in partials if r["job_id"] not in fresh_jobs]
    completed_final = [r for r in results.values() if r["kind"] == "final"]
    stages = defaultdict(float)
    for r in completed_final:
        n = (r.get("adapter_result") or {}).get("native_result") or {}
        for k in (
            "audio_encode_s",
            "audio_embedding_eval_s",
            "prefix_eval_s",
            "suffix_eval_s",
            "token_decode_s",
            "native_elapsed_s",
        ):
            if k in n:
                stages[k] += n[k]
    pred = worker.get("partial_scheduler", {})
    return {
        "schema": 1,
        "duration_s": duration,
        "sources": len(sources),
        "source_time_origin": "원음 파일 시작 또는 녹음 시작, 음향 단어 시작 시각 미측정",
        "raw_first_any_s": delays(old_first),
        "meaningful_first_s": delays(first),
        "first_by_owned_s": delays(owned_first),
        "owned_first_only_sources": [sources[i]["id"] for i in sorted(owned_first.keys() - first)],
        "owned_first_scope": "보조 지표. 소유 PCM이 한 원음에만 겹친 요청의 첫 본문 발행까지 잰다. 빌린 앞 파일의 음성이 글자에 기여할 수 있어 어휘 귀속·정확도를 보장하지 않는다.",
        "onsets": onset_latencies(worker, records, first, word_starts or {}),
        "meaningful_four_s": delays(four),
        "missing_first": len(sources) - len(first),
        "missing_four": len(sources) - len(four),
        "ranked_first": ranks,
        "first_kinds": dict(Counter(e["kind"] for e in first.values())),
        "common_file_end_final_s": distribution(
            [at - sources[i]["end"] / rate for i, at in final_at.items()]
        ),
        "missing_file_final": len(sources) - len(final_at),
        "old_raw_fresh_gap_s": distribution(old_gaps),
        "meaningful_fresh_gap_s": distribution(gaps),
        "fresh_count": len(fresh),
        "gaps_over_3_s": sum(t > 3 for t in gaps),
        "gaps_over_5_s": sum(t > 5 for t in gaps),
        "during_vad_owned_gap_s": distribution(active_gaps),
        "input_age_at_fresh_s": distribution(
            [e["published_s"] - e["end_frame"] / rate for e in fresh]
        ),
        "input_age_at_10hz_s": distribution(ages),
        "age_samples_before_first": samples_without,
        "timeline_windows": timeline_windows,
        "longest_gaps": [
            {
                "from_s": times[i],
                "to_s": times[i + 1],
                "duration_s": gaps[i],
                "before": event_record(fresh[i - 1]) if i else None,
                "after": event_record(fresh[i]) if i < len(fresh) else None,
            }
            for i in worst
        ],
        "calls": {f"{kind}_{status}": value for (kind, status), value in counts.items()},
        "empty_successful": dict(
            Counter(
                r["kind"]
                for r in results.values()
                if r["status"] == "success" and not normalize(r.get("hypothesis") or "")
            )
        ),
        "partial_work": {
            "submitted": sum(e["type"] == "submitted" and e["kind"] == "partial" for e in records),
            "coalesced_before_start": sum(
                e["type"] == "coalesced" and e["kind"] == "partial" for e in records
            ),
            "with_fresh_display": len(partials) - len(without_fresh),
            "without_fresh_display": len(without_fresh),
            "without_fresh_inference_s": sum(r.get("inference_s") or 0 for r in without_fresh),
            "cancelled_after_fresh_display": sum(r["job_id"] in fresh_jobs for r in cancelled),
        },
        "cancelled_compute_s": sum(r.get("inference_s") or 0 for r in cancelled),
        "inference_s": sum(r.get("inference_s") or 0 for r in results.values()),
        "final_native_stages_s": dict(stages),
        "scheduler_decisions": pred.get("decisions"),
        "scheduler_deferred_s": pred.get("deferred_s"),
        "candidate_replacements": pred.get("candidate_replacements"),
        "terminal_final_segments": len({r["segment_id"] for r in completed_final}),
        "emitted_segments": len(windows),
        "missing_final_segments": sorted(
            {s["segment_id"] for s in windows} - {r["segment_id"] for r in completed_final}
        ),
        "stability": worker.get("caption_stability"),
        "scope": "서버 발행 시각. 문자·숫자 본문이 새로 생기고 요청의 소유 입력 끝점이 이전 최신점보다 전진한 표시만 최신 갱신이다. 같은 PCM의 후속 토큰·순수 삭제·구두점은 제외한다. 갱신 공백은 트랙 양끝 포함, 전체 확정 지연은 공통 원음 끝부터 겹치는 마지막 확정 반환까지의 부호 있는 값이다. 단어 정확도와 브라우저·음향 지연은 별도다.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--boundary-errors",
        action="store_true",
        help="정답과 확정 전사를 정렬해 구간 사이의 삽입 반복을 계산",
    )
    parser.add_argument(
        "--punctuation-reference",
        type=Path,
        help="원음 ID·audio_sha256·문장부호를 보존한 reference가 있는 JSONL",
    )
    parser.add_argument(
        "--seoul-manifest", type=Path, help="서울말뭉치 원본 TextGrid 주석 manifest"
    )
    args = parser.parse_args()
    p = args.run
    worker = json.loads((p / "worker.json").read_text())
    onsets = (
        annotated_word_starts(
            args.seoul_manifest, worker["sources"], worker["audio"]["sample_rate_hz"]
        )
        if args.seoul_manifest
        else {}
    )
    summary = continuity(worker, read_jsonl(p / "jobs.jsonl"), word_starts=onsets)
    if args.boundary_errors or args.punctuation_reference:
        refs = read_jsonl(args.punctuation_reference) if args.punctuation_reference else ()
        summary["boundary_errors"] = boundary_errors(worker, refs)
    if args.punctuation_reference:
        summary["punctuation_reference_sha256"] = digest(args.punctuation_reference)
    if args.seoul_manifest:
        summary["seoul_manifest_sha256"] = digest(args.seoul_manifest)
    summary["source_commit"] = worker.get("source_commit")
    summary["worker_status"] = worker.get("status")
    summary["complete_input"] = worker.get("pipeline", {}).get("complete")
    summary["hashes"] = {name: digest(p / name) for name in ("worker.json", "jobs.jsonl")}
    write_json(args.output, summary)
