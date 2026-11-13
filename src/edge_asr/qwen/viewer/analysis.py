"""연속 실행의 표시 이벤트·원음별 지연. 모델과 웹 서버에 의존하지 않는다."""

from edge_asr.metrics import distribution


def compact_event(row):
    keys = (
        "type",
        "job_id",
        "kind",
        "segment_id",
        "start_frame",
        "end_frame",
        "published_s",
        "displayed",
        "hypothesis",
        "status",
        "queue_wait_s",
        "inference_s",
        "submitted_monotonic_s",
        "started_monotonic_s",
        "prefix_replay_suppressed",
        "error",
    )
    value = {k: row[k] for k in keys if k in row}
    native = (row.get("adapter_result") or {}).get("native_result") or {}
    value["stages"] = {
        k: native[k]
        for k in (
            "first_generated_token_s",
            "prefix_eval_s",
            "suffix_eval_s",
            "audio_encode_s",
            "audio_embedding_eval_s",
            "token_decode_s",
        )
        if k in native
    }
    return value


def visible(row):
    return (
        row.get("type") in ("token", "result")
        and row.get("displayed", False)
        and bool((row.get("hypothesis") or "").strip())
        and row.get("status") != "failed"
    )


def source_metrics(sources, events):
    """기존 성공 호출·유일 귀속 첫 표시 정의를 유지. 미출력은 None으로 보존."""
    results = {e["job_id"]: e for e in events if e["type"] == "result"}
    rows = []
    for source in sources:
        overlaps = [
            e
            for e in events
            if e.get("start_frame", 0) < source["end"] and e.get("end_frame", 0) > source["start"]
        ]
        shown = [
            e
            for e in overlaps
            if visible(e)
            and results.get(e["job_id"], {}).get("status") == "success"
            and sum(e["start_frame"] < s["end"] and e["end_frame"] > s["start"] for s in sources)
            == 1
        ]
        first = min(shown, key=lambda e: e["published_s"], default=None)
        four = min(
            (e for e in shown if sum(c.isalnum() for c in e["hypothesis"]) >= 4),
            key=lambda e: e["published_s"],
            default=None,
        )
        finals = [e for e in overlaps if e["type"] == "result" and e["kind"] == "final"]
        first_result = results[first["job_id"]] if first else {}
        previous_s, end_s = source["start"] / 16000, source["end"] / 16000
        lengths, gaps = {}, []
        for e in sorted(shown, key=lambda e: e["published_s"]):
            at = e["published_s"]
            length = sum(c.isalnum() for c in e["hypothesis"])
            segment = e["segment_id"]
            if previous_s <= at <= end_s and length > lengths.get(segment, 0):
                gaps.append(at - previous_s)
                previous_s, lengths[segment] = at, length
        gaps.append(end_s - previous_s)
        rows.append(
            {
                **source,
                "first_s": first["published_s"] - source["start"] / 16000 if first else None,
                "first4_s": four["published_s"] - source["start"] / 16000 if four else None,
                "max_growth_gap_s": max(gaps),
                "growth_updates_during_input": len(gaps) - 1,
                "file_end_s": max(e["published_s"] for e in finals) - source["end"] / 16000
                if finals
                else None,
                "first_job_id": first["job_id"] if first else None,
                "first_text": first["hypothesis"] if first else None,
                "first_queue_wait_s": first_result.get("queue_wait_s"),
                "first_inference_s": first_result.get("inference_s"),
                "first_stages": first_result.get("stages", {}),
                "final_hypothesis": " ".join(e.get("hypothesis") or "" for e in finals),
                "ambiguous_final": any(
                    sum(
                        e["start_frame"] < s["end"] and e["end_frame"] > s["start"] for s in sources
                    )
                    != 1
                    for e in finals
                ),
            }
        )
    return {
        "rows": rows,
        **{
            k: distribution([r[k] for r in rows if r[k] is not None])
            for k in ("first_s", "first4_s", "file_end_s", "max_growth_gap_s")
        },
    }


def microphone_sources(worker, events):
    """완료 VAD/ASR 구간만 지연 집계한다. 전체 정답을 구간에 임의 배분하지 않는다."""
    spans = {
        e["segment_id"]: (e["start_frame"], e["end_frame"])
        for e in events
        if e["type"] == "result" and e["kind"] == "final"
    }
    spans.update(
        {
            s["segment_id"]: (s["owned_start_frame"], s["owned_end_frame"])
            for s in worker.get("segments", [])
        }
    )
    return [
        {"id": f"speech-{key + 1}", "reference": "", "start": start, "end": end}
        for key, (start, end) in sorted(spans.items())
    ]
