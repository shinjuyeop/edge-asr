"""온라인 VAD의 소유/문맥 좌표와 방출 이벤트를 모델 없이 검증한다."""

import re

from edge_asr.streaming import read_events


def validate_sources(folder, track, jobs, policy):
    if policy not in ("vad0", "vad-continuation200"):
        raise ValueError("지원하지 않는 온라인 VAD 정책")
    rows = read_events(folder / "vad-sources.jsonl")
    expected = []
    ready = len(rows) == len(track["sources"])
    for source_index, row in enumerate(rows):
        if source_index >= len(track["sources"]):
            raise ValueError("VAD 원본 수 초과")
        source = track["sources"][source_index]
        if any(row[k] != source[k] for k in ("id", "start", "end")):
            raise ValueError("VAD 원본 순서/좌표 오류")
        meta = row.get("pipeline", {})
        success = row["status"] == "success"
        ready = ready and success
        frames = meta.get("frames", 0)
        selection = "continuation" if policy == "vad-continuation200" else "all"
        context = 3200 if selection == "continuation" else 0
        if (
            not 0 <= frames <= source["end"] - source["start"]
            or meta.get("context_frames") != context
            or meta.get("context_selection", "all") != selection
            or meta.get("native_budget_frames") is not None
            or meta.get("ring_peak_frames", 0) > 80000 + context + 16000
            or row.get("reassembly_peak_frames", 0) > 3199
            or (success and (not meta["complete"] or frames != source["end"] - source["start"]))
            or not re.fullmatch("[a-f0-9]{64}", meta.get("processed_pcm_sha256", ""))
        ):
            raise ValueError("VAD 처리 범위/상한/문맥 검증 실패")
        last = 0
        previous_span = None
        previous_reason = None
        for ordinal, segment in enumerate(row["segments"]):
            a, b = segment["owned_start_frame"], segment["owned_end_frame"]
            applied = 0
            if selection == "continuation":
                span, continued = segment.get("vad_span_id"), segment.get("is_continuation")
                if (
                    type(span) is not int
                    or span < 0
                    or type(continued) is not bool
                    or (previous_span is not None and span < previous_span)
                    or continued != (span == previous_span)
                    or (continued and (previous_reason != "hard_limit" or a != last))
                ):
                    raise ValueError("VAD 강제 분할 후속 식별 오류")
                applied = 3200 if continued else 0
                if segment.get("applied_context_frames") != applied:
                    raise ValueError("VAD 문맥 길이 오류")
                previous_span, previous_reason = span, segment["reason"]
            if (
                not last <= a < b <= frames
                or b - a > 80000
                or segment["segment_id"] != ordinal
                or segment["context_start_frame"] != max(0, a - applied)
                or segment["context_end_frame"] != b
                or segment["payload_frames"] != b - max(0, a - applied)
                or not all(
                    re.fullmatch("[a-f0-9]{64}", segment.get(key, ""))
                    for key in ("payload_sha256", "owned_pcm_sha256")
                )
                or segment["reason"] not in ("hard_limit", "vad_end", "eof")
                or not b
                <= segment["emitted_at_audio_frame"]
                <= segment["available_input_frame"]
                <= frames
            ):
                raise ValueError("VAD 구간/방출 좌표 오류")
            index = len(expected)
            expected.append((source_index, source["id"], source["start"] + a, source["start"] + b))
            if index < len(jobs):
                job = jobs[index]
                if (
                    job.get("segment") != segment
                    or job.get("context_start") != source["start"] + max(0, a - applied)
                    or job.get("context_end") != source["start"] + b
                    or not 0 < job.get("emitted_ns", 0) <= job["submitted_ns"] <= job["at_ns"]
                    or not source["start"] + segment["available_input_frame"]
                    <= job.get("delivered_through_frame", -1)
                    <= source["end"]
                ):
                    raise ValueError("VAD 방출/Refine 작업 불일치")
            last = b
        if success and (
            meta["segments"] != len(row["segments"])
            or meta["hard_limit_emissions"]
            != sum(s["reason"] == "hard_limit" for s in row["segments"])
        ):
            raise ValueError("VAD 구간 집계 불일치")
    return expected, ready
