"""VAD 구간 주석 검증·채점. 배포 라벨 예비 비교와 사람이 검수한 정답을 구분한다."""

import hashlib
import json
import re
from datetime import datetime

RATE = 16000
LABELS = {"speech", "non_speech", "ignore"}


def validate_annotation(row):
    if not isinstance(row, dict):
        raise ValueError("주석 객체가 필요합니다.")
    for key in ("id", "audio_path", "category"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"{key} 문자열이 필요합니다.")
    if row.get("schema") != "vad-annotation-v1" or row.get("sample_rate_hz") != RATE:
        raise ValueError("vad-annotation-v1 / 16000Hz가 필요합니다.")
    if not isinstance(row.get("audio_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", row["audio_sha256"]
    ):
        raise ValueError("WAV SHA-256이 필요합니다.")
    n = row.get("frames")
    if type(n) is not int or n <= 0 or row.get("split") not in ("development", "holdout"):
        raise ValueError("양의 프레임 수와 개발/보류 구분이 필요합니다.")
    status = row.get("annotation_status")
    if status not in ("corpus_provisional", "unreviewed", "human_reviewed", "excluded"):
        raise ValueError("지원하지 않는 검수 상태")
    previous = 0
    regions = row.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("전체 WAV를 덮는 speech/non_speech/ignore 구간이 필요합니다.")
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("구간 객체가 필요합니다.")
        a, b = region.get("start_frame"), region.get("end_frame")
        if (
            type(a) is not int
            or type(b) is not int
            or a != previous
            or not 0 <= a < b <= n
            or region.get("label") not in LABELS
        ):
            raise ValueError("주석 구간의 틈·겹침·역전·범위 오류")
        if not isinstance(region.get("reason"), str) or not region["reason"].strip():
            raise ValueError("각 주석 구간의 출처/판정 사유가 필요합니다.")
        previous = b
    if previous != n:
        raise ValueError("WAV 끝까지 주석해야 합니다. 불명확한 부분은 ignore로 남기세요.")
    if status in ("unreviewed", "excluded") and any(r["label"] != "ignore" for r in regions):
        raise ValueError("미검수/제외 자료에는 확정된 음성·무음 라벨을 둘 수 없습니다.")
    if status == "human_reviewed" or (status == "excluded" and row.get("reviewer") is not None):
        for key in ("reviewer", "review_note", "reviewed_at"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("검수자·검수 시각·검수 설명이 필요합니다.")
        if (
            row.get("review_method") != "listening_and_waveform"
            or datetime.fromisoformat(row["reviewed_at"]).utcoffset() is None
        ):
            raise ValueError("청취/파형 검수와 시간대 있는 시각이 필요합니다.")
    elif any(row.get(k) is not None for k in ("reviewer", "reviewed_at", "review_method")):
        raise ValueError("예비/미검수 자료를 검수 완료로 기록하지 않습니다.")
    if status == "excluded" and (
        not isinstance(row.get("exclusion_reason"), str) or not row["exclusion_reason"].strip()
    ):
        raise ValueError("제외 사유가 필요합니다.")
    target = row.get("target_interval_frames")
    if target is not None and (
        not isinstance(target, list)
        or len(target) != 2
        or any(type(v) is not int for v in target)
        or not 0 <= target[0] < target[1] <= n
    ):
        raise ValueError("잘못된 목표 발화 범위")
    endpoint = row.get("endpoint_reference_frame")
    if endpoint is not None:
        speech = [r for r in regions if r["label"] == "speech"]
        if (
            status != "human_reviewed"
            or type(endpoint) is not int
            or not speech
            or endpoint != speech[-1]["end_frame"]
            or endpoint >= n
            or any(r["label"] != "non_speech" for r in regions if r["end_frame"] > endpoint)
        ):
            raise ValueError(
                "endpoint는 사람이 검수한 마지막 음성 끝과 이후 확실한 비음성 구간이 필요합니다."
            )
    return row


def load_annotations(path, root):
    blob = path.read_bytes()
    rows, seen = [], set()
    for i, line in enumerate(blob.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = validate_annotation(json.loads(line))
            if row["id"] in seen:
                raise ValueError("중복 ID")
            p = (root / row["audio_path"]).resolve()
            if not p.is_relative_to(root.resolve() / "data"):
                raise ValueError("data/ 아래의 WAV가 필요합니다.")
            rows.append(row)
            seen.add(row["id"])
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            raise ValueError(f"{path}:{i}: {error}") from error
    if not rows:
        raise ValueError("주석 목록이 비어 있습니다.")
    return rows, hashlib.sha256(blob).hexdigest()


def union(intervals):
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def intersection_length(a, b):
    i = j = total = 0
    while i < len(a) and j < len(b):
        total += max(0, min(a[i][1], b[j][1]) - max(a[i][0], b[j][0]))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def rates(counts):
    div = lambda a, b: a / b if b else None
    return {
        "speech_miss_fraction": div(counts["false_negative_frames"], counts["speech_frames"]),
        "non_speech_false_alarm_fraction": div(
            counts["false_positive_frames"], counts["non_speech_frames"]
        ),
    }


def score_segments(annotation, segments):
    validate_annotation(annotation)
    n = annotation["frames"]
    for s in segments:
        a, b = s.get("owned_start_frame"), s.get("owned_end_frame")
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= n:
            raise ValueError("잘못된 VAD 소유 구간")
        emitted = s.get("emitted_at_audio_frame")
        if type(emitted) is not int or not b <= emitted <= n:
            raise ValueError("잘못된 VAD 반환 시각")
    predictions = union([(s["owned_start_frame"], s["owned_end_frame"]) for s in segments])
    refs = {
        label: union(
            [
                (r["start_frame"], r["end_frame"])
                for r in annotation["regions"]
                if r["label"] == label
            ]
        )
        for label in LABELS
    }
    lengths = {k: sum(b - a for a, b in v) for k, v in refs.items()}
    tp = intersection_length(predictions, refs["speech"])
    fp = intersection_length(predictions, refs["non_speech"])
    counts = {
        "speech_frames": lengths["speech"],
        "non_speech_frames": lengths["non_speech"],
        "ignored_frames": lengths["ignore"],
        "true_positive_frames": tp,
        "false_negative_frames": lengths["speech"] - tp,
        "false_positive_frames": fp,
        "true_negative_frames": lengths["non_speech"] - fp,
        "predicted_in_ignored_frames": intersection_length(predictions, refs["ignore"]),
    }
    target = annotation.get("target_interval_frames")
    target_speech = (
        [
            [max(a, target[0]), min(b, target[1])]
            for a, b in refs["speech"]
            if max(a, target[0]) < min(b, target[1])
        ]
        if target
        else []
    )
    target_total = sum(b - a for a, b in target_speech)
    target_detected = intersection_length(predictions, target_speech)
    endpoint = {
        "audio_timeline_delay_s": None,
        "boundary_error_s": None,
        "wall_delay_s": None,
        "reason": "human_reviewed_endpoint_required",
    }
    if annotation.get("endpoint_reference_frame") is not None:
        end = annotation["endpoint_reference_frame"]
        matches = [
            s
            for s in segments
            if min(s["owned_end_frame"], end) > max(s["owned_start_frame"], refs["speech"][-1][0])
        ]
        last = max(matches, key=lambda s: s["owned_end_frame"]) if matches else None
        if last and last.get("endpoint_eligible") is True and not last.get("after_flush"):
            endpoint.update(
                audio_timeline_delay_s=(last["emitted_at_audio_frame"] - end) / RATE,
                boundary_error_s=(last["owned_end_frame"] - end) / RATE,
                reason="matched_final_reference_speech; audio_timeline_only",
            )
        else:
            endpoint["reason"] = "missed_final_speech_or_only_hard_limit_or_eof"
    return {
        "label_basis": annotation["annotation_status"],
        "gold": annotation["annotation_status"] == "human_reviewed",
        "counts": counts,
        **rates(counts),
        "emitted_segments": len(segments),
        "target_speech_frames": target_total,
        "target_detected_frames": target_detected,
        "target_fully_missed": target_detected == 0 if target_total else None,
        "endpoint": endpoint,
    }


def select_development(rows, mode):
    if mode not in ("reviewed", "provisional"):
        raise ValueError("검수/예비 모드가 필요합니다.")
    selected = [
        r for r in rows if r["split"] == "development" and r["annotation_status"] != "excluded"
    ]
    allowed = {"human_reviewed"} if mode == "reviewed" else {"corpus_provisional", "unreviewed"}
    if not selected or any(r["annotation_status"] not in allowed for r in selected):
        raise ValueError(
            "검수 상태와 실행 모드가 다릅니다. 미검수 예비 점검은 --mode provisional로 명시하세요."
        )
    return selected
