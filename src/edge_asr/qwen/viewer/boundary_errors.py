"""확정 원문의 경계 반복과 정답 문장부호를 계산한다. 모델 입력·표시에는 관여하지 않는다."""

import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter

from edge_asr.metrics import distribution, normalize
from edge_asr.qwen.viewer.analysis import visible


def word_display_delays(annotated, records, rate):
    """두 연속 단어의 고유 본문을 표시한 첫 시각과 두 번째 단어의 음향 끝을 비교한다."""
    anchors = []
    for source, words in annotated.items():
        reference = "".join(text for _, _, text in words)
        for index, (previous, current) in enumerate(zip(words, words[1:]), 1):
            phrase = previous[2] + current[2]
            if len(phrase) >= 3 and reference.find(phrase) == reference.rfind(phrase):
                anchors.append((current[1], previous[0], source, index, phrase))
    anchors.sort()
    ends = [a[0] for a in anchors]
    seen = {}
    events = sorted(
        (
            e
            for e in records
            if visible(e) and not e.get("stale_revision") and e["status"] != "cancelled"
        ),
        key=lambda e: e["published_s"],
    )
    for e in events:
        start = e.get("owned_start_frame", e["start_frame"])
        text = normalize(e["hypothesis"])
        for end, begin, source, index, phrase in anchors[
            bisect_right(ends, start) : bisect_right(ends, e["end_frame"])
        ]:
            if (source, index) in seen or begin < start or e["published_s"] < end / rate:
                continue
            if phrase in text and text.find(phrase) == text.rfind(phrase):
                seen[source, index] = dict(
                    source=source,
                    word_index=index,
                    audio_end_frame=end,
                    published_s=e["published_s"],
                    delay_s=e["published_s"] - end / rate,
                    job_id=e["job_id"],
                    kind=e["kind"],
                )
    return {
        "annotated_words": sum(map(len, annotated.values())),
        "unique_pairs": len(anchors),
        "first_visible_word_end_delay_s": distribution([x["delay_s"] for x in seen.values()]),
        "observations": list(seen.values()),
    }


def aligned_edits(reference, hypothesis):
    """ko_cer_v1과 같은 동률 순서로 가설 문자 사이의 정답 좌표와 원래 편집 연산을 복원한다."""
    previous = list(range(len(hypothesis) + 1))
    trace = []
    for i, left in enumerate(reference, 1):
        current = [i]
        directions = bytearray([1])
        for j, right in enumerate(hypothesis, 1):
            diagonal = previous[j - 1] + (left != right)
            deletion = previous[j] + 1
            insertion = current[-1] + 1
            best = min(diagonal, deletion, insertion)
            current.append(best)
            directions.append(0 if best == diagonal else 1 if best == deletion else 2)
        trace.append(directions)
        previous = current

    i, j = len(reference), len(hypothesis)
    ends = [0] * (j + 1)
    edits = []
    while j:
        ends[j] = i
        # 역추적할 때 같은 가설 틈에 대응하는 가장 오른쪽 정답 좌표를 보존한다.
        while i and trace[i - 1][j] == 1:
            i -= 1
            edits.append(("D", i, j))
        if not i or trace[i - 1][j] == 2:
            edits.append(("I", i, j - 1))
        else:
            i -= 1
            if reference[i] != hypothesis[j - 1]:
                edits.append(("S", i, j - 1))
        j -= 1
    ends[0] = i
    edits.extend(("D", k, 0) for k in range(i - 1, -1, -1))
    return ends, edits[::-1]


def edit_coordinates(reference, hypothesis):
    ends, edits = aligned_edits(reference, hypothesis)
    return ends, {j for op, i, j in edits if op == "I"}, len(edits)


def punctuation(text):
    text = unicodedata.normalize("NFC", text)
    for mark in re.finditer(r"[.!?。？！…]+|[,，;；:：]+", text):
        start, end = mark.span()
        if start and end < len(text) and text[start - 1].isdigit() and text[end].isdigit():
            continue
        family = "sentence" if mark[0][0] in ".!?。？！…" else "clause"
        yield len(normalize(text[:start])), family


def cut_words(annotated, segments):
    cuts = sorted(
        (s["owned_end_frame"], s["segment_id"], s["reason"])
        for s in segments
        if s["reason"] in ("hard_limit", "pause_before_limit")
    )
    frames = [cut[0] for cut in cuts]
    rows = []
    for source, words in annotated.items():
        for index, (lo, hi, text) in enumerate(words):
            inside = cuts[bisect_right(frames, lo) : bisect_left(frames, hi)]
            if inside:
                rows.append(
                    dict(
                        source=source,
                        word_index=index,
                        text=text,
                        start_frame=lo,
                        end_frame=hi,
                        cuts=[dict(frame=f, segment_id=i, reason=r) for f, i, r in inside],
                    )
                )
    return rows


def boundary_errors(worker, references=()):
    score = worker.get("final_transcript_score")
    if not score or worker.get("status") != "completed":
        raise ValueError("경계 평가에는 정답과 배출이 완료된 실행이 필요합니다.")
    finals = sorted(
        (r for r in worker["results"] if r["kind"] == "final" and r["status"] == "success"),
        key=lambda r: r["segment_id"],
    )
    texts = [normalize(r["hypothesis"]) for r in finals]
    hyp = "".join(texts)
    sources = worker["sources"]
    ref = "".join(normalize(s["reference"]) for s in sources)
    if ref != score["normalized_reference"] or hyp != score["normalized_hypothesis"]:
        raise ValueError("원시 확정/정답이 보존된 전체 채점 문자열과 다릅니다.")
    coords, edits = aligned_edits(ref, hyp)
    inserted = {j for op, i, j in edits if op == "I"}
    if len(edits) != score["errors"]:
        raise ValueError("경계 정렬과 원래 문자 오류 수가 다릅니다.")

    ends = []
    gold = {}
    target = Counter()
    word_ends = set()
    oracle = {}
    for row in references:
        if row["id"] in oracle:
            raise ValueError("문장부호 주석의 원음 ID가 중복되었습니다.")
        oracle[row["id"]] = row
    for index, src in enumerate(sources):
        start = ends[-1] if ends else 0
        actual = normalize(src["reference"])
        ends.append(start + len(actual))
        word_ends.add(start)
        pos = start
        for word in src["reference"].split():
            pos += len(normalize(word))
            word_ends.add(pos)
        row = oracle.get(src["id"])
        if row is None:
            continue
        if row["audio_sha256"] != src["audio_sha256"]:
            raise ValueError(f"문장부호 주석의 원음 해시가 다릅니다: {src['id']}")
        raw = normalize(row["reference"])
        if len(raw) != len(actual) or raw.lower() != actual.lower():
            raise ValueError(f"문장부호 주석의 본문이 다릅니다: {src['id']}")
        gold[index] = src["id"]
        for pos, family in punctuation(row["reference"]):
            target[index, start + pos, family] += 1

    joined = []
    predicted = Counter()
    windows = []
    word_internal_terminals = 0
    cursor = 0
    for idx, result in enumerate(finals):
        for pos, family in punctuation(result["hypothesis"]):
            at = coords[cursor + pos]
            owner = bisect_left(ends, at)
            if at and owner in gold:
                predicted[owner, at, family] += 1
        left = texts[idx]
        cursor += len(left)
        if idx + 1 == len(finals):
            continue
        nxt = finals[idx + 1]
        # 실패하거나 건너뛴 중간 구간 양쪽을 하나의 확정 경계로 세지 않는다.
        if nxt["segment_id"] != result["segment_id"] + 1:
            continue
        next_start = nxt.get("owned_start_frame", nxt["start_frame"])
        owner = next(
            (
                i
                for i, s in enumerate(sources)
                if s["start"] < result["end_frame"] <= next_start < s["end"]
            ),
            None,
        )
        right = texts[idx + 1]
        if owner is None:
            continue
        start = ends[owner - 1] if owner else 0
        end = ends[owner]
        at = coords[cursor]
        if not start <= at <= end:
            continue
        windows.append((max(start, at - 3), min(end, at + 3)))
        if at not in word_ends and re.search(r"[.!?。？！…][\s\"”’'»)）]*$", result["hypothesis"]):
            word_internal_terminals += 1
        length = next(
            (k for k in range(min(len(left), len(right)), 0, -1) if left[-k:] == right[:k]), 0
        )
        if length:
            extra = all(j in inserted for j in range(cursor - length, cursor)) or all(
                j in inserted for j in range(cursor, cursor + length)
            )
            joined.append(
                {
                    "source": sources[owner]["id"],
                    "left_segment": result["segment_id"],
                    "right_segment": nxt["segment_id"],
                    "text": right[:length],
                    "copy_aligned_as_insertion": extra,
                }
            )
    families = {}
    for family in ("sentence", "clause"):
        a = Counter({k: v for k, v in target.items() if k[2] == family})
        b = Counter({k: v for k, v in predicted.items() if k[2] == family})
        families[family] = {
            "reference": a.total(),
            "predicted": b.total(),
            "matched": (a & b).total(),
            "extra": (b - a).total(),
            "missing": (a - b).total(),
        }
    return {
        "same_source_join_windows": windows,
        "word_internal_terminals": word_internal_terminals,
        "reference_edits": [(op, i) for op, i, j in edits],
        "adjacent_repetitions": joined,
        "inserted_copies": sum(r["copy_aligned_as_insertion"] for r in joined),
        "punctuation_reference_sources": list(gold.values()),
        "punctuation": families,
    }


def paired_windows(baseline, candidate, before, after):
    """이전·새 경계를 같은 정답 문자 위치에서 비교한다. 삽입은 다음 정답 문자 좌표다."""
    fields = ("id", "audio_sha256", "start", "end", "reference")
    sources = [
        [tuple(row[k] for k in fields) for row in w["sources"]] for w in (baseline, candidate)
    ]
    if sources[0] != sources[1] or baseline["audio"]["sha256"] != candidate["audio"]["sha256"]:
        raise ValueError("경계 대조에는 같은 정답·원음·입력 순서와 PCM이 필요합니다.")
    old, new = (
        {i for lo, hi in report["same_source_join_windows"] for i in range(lo, hi)}
        for report in (before, after)
    )
    regions = {"baseline": old, "candidate": new, "introduced": new - old, "removed": old - new}
    output = {}
    for name, positions in regions.items():
        output[name] = {"reference_characters": len(positions)}
        for arm, report in (("baseline", before), ("candidate", after)):
            output[name][arm] = dict(
                Counter(op for op, i in report["reference_edits"] if i in positions)
            )
    return output
