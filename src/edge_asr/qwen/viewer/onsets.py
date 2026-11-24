"""원음·사람의 첫 단어 주석·VAD 소유 범위·실제 부분 요청 기회를 같은 시계에 연결한다."""

from collections import defaultdict

from edge_asr.artifacts import digest, read_jsonl
from edge_asr.metrics import distribution, normalize
from edge_asr.textgrid import label_kind, read_textgrid


def annotated_words(manifest, sources, rate):
    """소스 해시·전체 전사와 일치하는 서울말뭉치 화자의 pWord.ortho. 주석만 인정한다."""
    by_id = {r["id"]: r for r in read_jsonl(manifest) if "label_member" in r}
    annotated = {}
    for source in sources:
        row = by_id.get(source["id"])
        if row is None:
            continue
        lo, hi = row["source_pcm16_frames"]
        if (
            row["audio_sha256"] != source["audio_sha256"]
            or hi - lo != source["end"] - source["start"]
            or normalize(row["reference"]) != normalize(source["reference"])
        ):
            raise ValueError(f"음성·정답이 주석 manifest와 다릅니다: {source['id']}")
        path = manifest.parent / "original" / row["label_member"]
        if digest(path) != row["label_sha256"]:
            raise ValueError(f"원본 TextGrid 해시가 다릅니다: {path}")
        matches = []
        for tier in read_textgrid(path.read_bytes()).tiers:
            if tier.name != "pWord.ortho.":
                continue
            words = [
                (
                    source["start"] + max(lo, round(word.start * rate)) - lo,
                    source["start"] + min(hi, round(word.end * rate)) - lo,
                    normalize(word.text),
                )
                for word in tier.intervals
                if max(lo, round(word.start * rate)) < min(hi, round(word.end * rate))
                and label_kind(word.text) == "speech"
                and normalize(word.text)
            ]
            if words and "".join(w[2] for w in words) == normalize(row["reference"]):
                matches.append(words)
        if len(matches) != 1:
            raise ValueError(f"전체 정답과 일치하는 단어 계층이 하나여야 합니다: {source['id']}")
        annotated[source["id"]] = matches[0]
    return annotated


def annotated_word_starts(manifest, sources, rate):
    return {key: words[0][0] for key, words in annotated_words(manifest, sources, rate).items()}


def onset_latencies(worker, records, first, word_starts):
    rate = worker["audio"]["sample_rate_hz"]
    t0_ns = worker.get("telemetry", {}).get("t0_monotonic_ns")
    t0 = t0_ns / 1e9 if t0_ns is not None else None
    active, eligible, submitted = {}, {}, {}
    work = []
    for e in records:
        typ = e["type"]
        if typ == "active_span":
            active.setdefault(e["segment_id"], e["at_monotonic_s"])
        elif typ == "partial_audio_eligible" and e["previous_end_frame"] == e["start_frame"]:
            eligible.setdefault(e["segment_id"], e["at_monotonic_s"])
        elif typ == "submitted" and e["kind"] == "partial":
            submitted.setdefault(e["segment_id"], e["job_id"])
        elif typ == "result" and "started_monotonic_s" in e and "ended_monotonic_s" in e:
            work.append((e["started_monotonic_s"], e["ended_monotonic_s"]))

    segments = []
    previous = {}
    for seg in worker.get("segments", []):
        continuation = seg.get(
            "is_continuation",
            previous.get("reason") in ("hard_limit", "pause_before_limit")
            and previous["owned_end_frame"] == seg["owned_start_frame"],
        )
        segments.append((seg, continuation))
        previous = seg
    sums = defaultdict(list)
    rows = []
    for index, source in enumerate(worker["sources"]):
        lo, hi = source["start"], source["end"]
        row = {"source": source["id"], "file_start_s": lo / rate}
        rows.append(row)
        event = first.get(index)
        if event is None:
            row["scope"] = "no_unique_meaningful_display"
            continue
        at = event["published_s"]
        row.update(first_at_s=at, first_job=event["job_id"], first_segment=event["segment_id"])
        sums["file_to_first"].append(at - lo / rate)
        word = word_starts.get(source["id"])
        if word is not None and event["end_frame"] > word and at >= word / rate:
            row["first_word_at_s"] = word / rate
            sums["word_to_first"].append(at - word / rate)
        initial = next(
            (
                (s, c)
                for s, c in segments
                if s["owned_end_frame"] > lo and s["owned_start_frame"] < hi
            ),
            None,
        )
        if initial is None or initial[1] or initial[0]["owned_start_frame"] < lo:
            row["scope"] = "no_source_local_initial_vad_onset"
            continue
        seg = initial[0]
        sid = seg["segment_id"]
        onset = seg["owned_start_frame"] / rate
        if event["end_frame"] <= seg["owned_start_frame"] or at < onset:
            row["scope"] = "first_display_precedes_vad_onset"
            continue
        row.update(vad_owned_at_s=onset, vad_segment=sid)
        sums["file_to_vad_owned"].append(onset - lo / rate)
        sums["vad_owned_to_first"].append(at - onset)
        if sid not in active or t0 is None or not (onset <= active[sid] - t0 <= at):
            row["scope"] = "no_observed_initial_active_span_before_display"
            continue
        detected = active[sid]
        row["vad_observed_at_s"] = detected - t0
        sums["vad_owned_to_observed"].append(detected - t0 - onset)
        if "first_word_at_s" in row:
            sums["word_to_vad_observed"].append(detected - t0 - row["first_word_at_s"])
        opportunity = eligible.get(sid)
        if opportunity is None or not (detected <= opportunity <= t0 + at):
            row["scope"] = "no_initial_partial_opportunity_before_display"
            continue
        row["eligible_at_s"] = opportunity - t0
        sums["vad_observed_to_eligible"].append(opportunity - detected)
        submit, start = event.get("submitted_monotonic_s"), event.get("started_monotonic_s")
        if (
            event["kind"] != "partial"
            or submitted.get(sid) != event["job_id"]
            or submit is None
            or start is None
            or not opportunity <= submit <= start <= t0 + at
        ):
            row["scope"] = "first_display_from_another_request"
            continue
        # 하나인 워커가 실제 추론 중이던 시간만 분리한다. 유휴 나머지는 원인을 단정하지 않는다.
        busy = sum(max(0, min(b, submit) - max(a, opportunity)) for a, b in work)
        row.update(submitted_at_s=submit - t0, started_at_s=start - t0, busy_until_submit_s=busy)
        sums["eligible_to_submit"].append(submit - opportunity)
        sums["worker_busy_before_submit"].append(busy)
        sums["submit_to_start"].append(start - submit)
        sums["start_to_first"].append(at + t0 - start)
        row["scope"] = "same_initial_partial"
    names = (
        "file_to_first",
        "word_to_first",
        "file_to_vad_owned",
        "vad_owned_to_first",
        "vad_owned_to_observed",
        "word_to_vad_observed",
        "vad_observed_to_eligible",
        "eligible_to_submit",
        "worker_busy_before_submit",
        "submit_to_start",
        "start_to_first",
    )
    return {
        "seconds": {k: distribution(sums[k]) for k in names},
        "sources": rows,
        "scope": "첫 표시는 동일한 의미 있는 서버 이벤트다. owned는 역산한 VAD 시작 프레임, observed는 첫 활성 범위 콜백이다. 기회부터 제출까지는 같은 최초 부분 요청으로 연결된 경우만 측정한다. word는 검증된 pWord.ortho. 첫 단어 주석이며 결과 토큰 자체의 음향 정렬은 측정하지 않는다.",
    }
