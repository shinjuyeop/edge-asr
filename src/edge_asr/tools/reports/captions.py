"""저장된 모델/자막/표시 이벤트를 대조한다. 새 추론이나 입력 장치를 실행하지 않는다."""

import json
import tempfile
from pathlib import Path

from edge_asr.metrics import distribution
from edge_asr.streaming import read_events


def summarize(directory, reference_available):
    from edge_asr.tools.reports.concurrent import summarize as summarize_native

    runtime = directory / "runtime"
    worker = json.loads((runtime / "worker.json").read_text())
    native = json.loads((runtime / "summary.json").read_text())
    with tempfile.TemporaryDirectory() as temporary:
        if summarize_native(runtime, Path(temporary) / "summary.json") != native:
            raise ValueError("실제 추론 원시 재집계 불일치")
    if (
        worker["status"] != "completed"
        or not native["input_complete"]
        or not native["refine"]["complete"]
    ):
        raise ValueError("미완료 추론을 정상 표시로 집계할 수 없습니다.")
    track = json.loads((runtime / "track.json").read_text())
    events = read_events(runtime / "caption-events.jsonl")
    shown = read_events(directory / "terminal-events.jsonl")
    if len(events) != len(shown) or [e["event_index"] for e in events] != list(range(len(events))):
        raise ValueError("터미널 표시 이벤트 누락/순서 오류")
    if any(
        s["event"] != e or s["stdout_flushed_ns"] < s["stdout_started_ns"]
        for e, s in zip(events, shown)
    ):
        raise ValueError("원본 표시 이벤트/flush 기록 불일치")
    sources = {s["id"]: s for s in track["sources"]}
    finals = {e["id"]: e["hypothesis"] for e in read_events(runtime / "zip-sources.jsonl")}
    displayed_finals = {e["source_id"]: e["text"] for e in events if e["kind"] == "zip_final"}
    if finals != displayed_finals or set(finals) != set(sources):
        raise ValueError("Zip 최종 원문 표시 불일치")
    previous = {}
    expected_zip = []
    for row in read_events(runtime / "zip-events.jsonl"):
        if row["type"] == "partial":
            sid = track["sources"][row["source_index"]]["id"]
            if row["text"] and previous.get(sid) != row["text"]:
                expected_zip.append(("zip_partial", sid, row["text"], row["at_ns"]))
                previous[sid] = row["text"]
        elif row["type"] == "source_final":
            expected_zip.append(("zip_final", row["id"], row["hypothesis"], row["finalized_ns"]))
    if expected_zip != [
        (e["kind"], e["source_id"], e["text"], e["available_ns"])
        for e in events
        if e["kind"].startswith("zip_")
    ]:
        raise ValueError("Zip 부분/최종 표시가 실제 모델 반환과 다릅니다.")
    results = {r["index"]: r for r in read_events(runtime / "refine-results.jsonl")}
    candidates = {
        e["job_id"]: e for e in events if e["kind"] in ("refine_candidate", "refine_failed")
    }
    if set(results) != set(candidates) or any(
        candidates[i]["text"] != (r.get("hypothesis") or "") for i, r in results.items()
    ):
        raise ValueError("보정 원문 표시 불일치")
    t0 = worker["replay"]["t0_monotonic_ns"]
    first = {}
    dispatch, refine = [], []
    for row in shown:
        e, flushed = row["event"], row["stdout_flushed_ns"]
        dispatch.append((flushed - e["available_ns"]) / 1e9)
        if e["kind"] == "zip_partial" and e["text"]:
            first.setdefault(
                e["source_id"], (flushed - t0) / 1e9 - sources[e["source_id"]]["start"] / 16000
            )
        if e["kind"] == "refine_candidate":
            end = sources[e["source_id"]]["start"] / 16000 + e["source_end_s"]
            refine.append((flushed - t0) / 1e9 - end)
    return {
        "schema": "separate-caption-mvp-v1",
        "status": "completed",
        "source_commit": worker["source_commit"],
        "reference_available": reference_available,
        "reference_policy": "manifest_reference"
        if reference_available
        else "empty_placeholder_not_silence_ground_truth",
        "sources": len(sources),
        "audio_s": worker["audio_s"],
        "display_events": len(events),
        "zip_final_displays": len(displayed_finals),
        "refine_candidate_displays": len(candidates),
        "automatic_replacements": 0,
        "first_zip_stdout_from_audio_start_s": distribution(list(first.values())),
        "available_to_stdout_flush_s": distribution(dispatch),
        "refine_owned_end_to_stdout_s": distribution(refine),
        "zip_cer": native["zip_cer"] if reference_available else None,
        "native_metrics": native,
        "unmeasured": {
            "screen_paint_s": None,
            "acoustic_recognition_delay_s": None,
            "merged_cer": None,
            "microphone_overruns": None,
        },
        "scope_ko": "실제 1배속 입력/NN/부모 stdout flush. 화면 렌더링·SSH 도착은 미계측. 모델 PID CPU/RSS는 터미널 부모/guard를 제외.",
    }
