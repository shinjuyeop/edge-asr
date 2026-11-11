"""표시 접두부의 재생 여부와 저장 이벤트의 문자 철회량을 구분한다."""

from edge_asr.metrics import distribution, normalize


def replays_prefix(previous, event):
    """다른 호출이 같은 구간의 이미 보인 문자열 접두부를 다시 생성하는가."""
    return bool(
        previous
        and event.get("hypothesis")
        and previous.get("hypothesis")
        and previous["segment_id"] == event["segment_id"]
        and previous["job_id"] != event["job_id"]
        and previous["hypothesis"].startswith(event["hypothesis"])
    )


def prefix_length(left, right):
    return next(
        (i for i, (a, b) in enumerate(zip(left, right)) if a != b),
        min(len(left), len(right)),
    )


def summarize(events):
    """같은 구간의 표시 변경만 비교한다. 최종 전사와의 일치는 정답 정확도가 아니다."""
    previous, histories, partials, finals = {}, {}, {}, {}
    revised, replayed, held_jobs = set(), set(), set()
    counts = {
        "displayed_updates": 0,
        "normalized_revisions": 0,
        "retracted_suffix_characters": 0,
        "prefix_replay_revisions": 0,
        "prefix_replay_retracted_characters": 0,
        "within_call_revisions": 0,
        "failed_results": 0,
        "suppressed_prefix_tokens": 0,
    }
    for row in events:
        if row["type"] not in ("token", "result"):
            continue
        segment = row["segment_id"]
        if row.get("prefix_replay_suppressed"):
            counts["suppressed_prefix_tokens"] += 1
            held_jobs.add(row["job_id"])
        if row["type"] == "result":
            counts["failed_results"] += row["status"] != "success"
            if row["kind"] == "final":
                finals[segment] = row
            elif row["status"] == "success":
                partials[segment] = row
        if not row["displayed"]:
            continue
        text = normalize(row.get("hypothesis") or "")
        history = histories.setdefault(segment, [])
        if history and row["published_s"] < history[-1]["published_s"]:
            raise ValueError("같은 구간의 표시 시각이 역행했습니다.")
        old = previous.get(segment)
        if old:
            before = normalize(old.get("hypothesis") or "")
            removed = len(before) - prefix_length(before, text)
            if removed:
                counts["normalized_revisions"] += 1
                counts["retracted_suffix_characters"] += removed
                counts["within_call_revisions"] += old["job_id"] == row["job_id"]
                revised.add(segment)
                if replays_prefix(old, row):
                    counts["prefix_replay_revisions"] += 1
                    counts["prefix_replay_retracted_characters"] += removed
                    replayed.add(segment)
        counts["displayed_updates"] += 1
        history.append({"text": text, "published_s": row["published_s"]})
        previous[segment] = row

    agreements, stable_lags = [], []
    short_finals = 0
    for segment, final in finals.items():
        if final["status"] != "success":
            continue
        text = normalize(final.get("hypothesis") or "")
        if segment in partials:
            partial = normalize(partials[segment]["hypothesis"])
            agreements.append((len(partial), prefix_length(partial, text)))
        if len(text) < 4:
            short_finals += 1
            continue
        history = histories.get(segment, [])
        first4 = next((r["published_s"] for r in history if len(r["text"]) >= 4), None)
        stable4, retained = None, len(text)
        for row in reversed(history):
            retained = min(retained, prefix_length(row["text"], text))
            if retained >= 4:
                stable4 = row["published_s"]
        if first4 is not None and stable4 is not None:
            stable_lags.append(stable4 - first4)
    total_partial = sum(length for length, _ in agreements)
    retained_partial = sum(length for _, length in agreements)
    return {
        "normalization": "ko_cer_v1",
        **counts,
        "segments_with_display": len(histories),
        "revised_segments": len(revised),
        "prefix_replay_segments": len(replayed),
        "suppressed_prefix_jobs": len(held_jobs),
        "latest_completed_partial_vs_final": {
            "paired_segments": len(agreements),
            "whole_partial_is_final_prefix": sum(a == b for a, b in agreements),
            "partial_characters": total_partial,
            "retained_prefix_characters": retained_partial,
            "retained_prefix_fraction": retained_partial / total_partial if total_partial else None,
        },
        "finals_shorter_than4": short_finals,
        "first4_to_final_consistent4_s": distribution(stable_lags),
        "scope": "같은 구간의 ko_cer_v1 공통 접두부 뒤 철회 문자수이며 편집 거리·부분 CER가 아님. 4자 보존은 자기 최종 전사 기준 후향 평가이며 정답·단어 음향·GUI 지연이 아님. 실패는 별도 집계하고 해당 구간의 성공 일치율에서 제외.",
    }
