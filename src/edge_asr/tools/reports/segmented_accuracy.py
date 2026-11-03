"""구간별 전사를 원본 발화 정답으로 재채점한다. 모델·음원·원래 manifest는 불필요하다."""

import argparse
import math
import re
from pathlib import Path

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.scoring import read_predictions, score_records

# 실행기 import 없이 고정 실험 계약을 독립 검증한다.
CONDITIONS = ("full_batch", "fixed_windows_5s", "bounded_vad_5s", "bounded_vad_5s_context200ms")
RATE, MAXIMUM = 16000, 80000


def verify(row):
    if row.get("segmentation_condition") not in CONDITIONS:
        raise ValueError("구간 평가 조건이 없습니다.")
    if row["status"] != "success":
        return
    meta = row["segmented"]
    frames = row["audio"]["frames"]
    context = 3200 if row["segmentation_condition"] == "bounded_vad_5s_context200ms" else 0
    is_vad = row["segmentation_condition"].startswith("bounded_vad_")
    if (
        row["audio"]["sample_rate_hz"] != RATE
        or meta["condition"] != row["segmentation_condition"]
        or meta["input_frames"] != frames
        or meta["context_frames"] != context
        or not re.fullmatch("[a-f0-9]{64}", meta["input_pcm_sha256"])
    ):
        raise ValueError("입력/조건/PCM 메타데이터 불일치")
    last, selected, payloads = 0, 0, 0
    for i, segment in enumerate(meta["segments"]):
        a, b = segment["owned_start_frame"], segment["owned_end_frame"]
        if (
            type(a) is not int
            or type(b) is not int
            or not last <= a < b <= frames
            or segment["segment_id"] != i
            or segment["context_start_frame"] != max(0, a - context)
            or segment["context_end_frame"] != b
            or segment["payload_frames"] != b - max(0, a - context)
            or not re.fullmatch("[a-f0-9]{64}", segment["payload_sha256"])
            or segment["status"] != "success"
            or not isinstance(segment["hypothesis"], str)
            or (meta["condition"] != "full_batch" and b - a > MAXIMUM)
        ):
            raise ValueError("구간 경계/순서/상한/출력 메타데이터 오류")
        if not is_vad and a != last:
            raise ValueError("전체/고정 창 조건의 프레임 누락")
        last, selected = b, selected + b - a
        payloads += segment["payload_frames"]
    if selected != meta["selected_frames"] or (not is_vad and selected != frames):
        raise ValueError("선택 프레임 수 불일치")
    if meta.get("payload_frames_total", payloads) != payloads:
        raise ValueError("문맥을 포함한 payload 프레임 합 불일치")
    if meta["condition"] == "full_batch" and len(meta["segments"]) != 1:
        raise ValueError("전체 발화는 ASR 한 번이어야 합니다.")
    if row["hypothesis"] != " ".join(s["hypothesis"] for s in meta["segments"]):
        raise ValueError("구간 전사 연결 정책 불일치")
    if is_vad:
        pipeline = meta["pipeline"]
        if (
            not pipeline["complete"]
            or pipeline["frames"] != frames
            or pipeline["processed_pcm_sha256"] != meta["input_pcm_sha256"]
            or pipeline["context_frames"] != context
            or pipeline["native_budget_frames"] is not None
            or pipeline["ring_peak_frames"] > pipeline["ring_capacity_frames"]
        ):
            raise ValueError("VAD 파이프라인 검증 실패")
    for key in ("asr_wall_s", "asr_cpu_s", "pipeline_wall_s", "pipeline_cpu_s"):
        if type(meta[key]) not in (int, float) or not math.isfinite(meta[key]) or meta[key] < 0:
            raise ValueError("비유한/음수 처리 비용")
    if not math.isclose(
        sum(s["inference_s"] for s in meta["segments"]), meta["asr_wall_s"], abs_tol=1e-8
    ):
        raise ValueError("구간 ASR 시간 합 불일치")
    if (
        row["elapsed_s"] != meta["pipeline_wall_s"]
        or row["process_cpu_s"] != meta["pipeline_cpu_s"]
    ):
        raise ValueError("공통 scorer에 전달한 시간 범위 오류")


def summarize(predictions, output):
    if (output / "summary.json").exists():
        raise FileExistsError("기존 채점 결과를 덮어쓰지 않습니다.")
    rows = read_predictions(predictions)
    if len({r["segmentation_condition"] for r in rows}) != 1:
        raise ValueError("서로 다른 구간 정책을 한 CER로 합치지 않습니다.")
    for row in rows:
        verify(row)
    summary, scores = score_records(rows, "ko_cer_v1")
    successful = [r for r in rows if r["status"] == "success"]
    audio_s = sum(r["audio"]["duration_s"] for r in successful)
    asr_s = sum(r["segmented"]["asr_wall_s"] for r in successful)
    vad_costs = [r["segmented"]["vad_inference_s"] for r in successful]
    summary["segmented_accuracy"] = {
        "condition": rows[0]["segmentation_condition"],
        "predictions_sha256": digest(predictions),
        "asr_calls": sum(len(r["segmented"]["segments"]) for r in successful),
        "empty_segment_files": sum(not r["segmented"]["segments"] for r in successful),
        "selected_frames": sum(r["segmented"]["selected_frames"] for r in successful),
        "asr_wall_s": asr_s,
        "asr_rtf_against_original_audio": asr_s / audio_s if audio_s else None,
        "vad_neural_wall_s": sum(vad_costs)
        if vad_costs and all(x is not None for x in vad_costs)
        else None,
        "timing_scope": "overall.rtf는 원본 오디오 길이 대비 전처리/구간화/ASR/결과 연결/검증/계측을 포함한 파일 파이프라인 비용. asr_rtf는 ASR 호출만. WAV I/O/검증용 참조 PCM/모델 로딩/워밍업/파일 기록 제외.",
        "rss_scope": "ASR+VAD 공존 프로세스"
        if rows[0]["segmentation_condition"].startswith("bounded_vad_")
        else "ASR 프로세스",
    }
    # 과거 문맥 0의 저장된 요약 형식은 그대로 유지한다.
    if rows[0]["segmentation_condition"] == "bounded_vad_5s_context200ms":
        summary["segmented_accuracy"].update(
            context_frames=3200,
            payload_frames_total=sum(r["segmented"]["payload_frames_total"] for r in successful),
        )
    summary["by_length"] = {}
    for name, predicate in (("at_most_5s", lambda n: n <= 5), ("over_5s", lambda n: n > 5)):
        subset = [r for r in rows if r.get("audio") and predicate(r["audio"]["duration_s"])]
        summary["by_length"][name] = (
            score_records(subset, "ko_cer_v1")[0]["overall"] if subset else None
        )
    summary["unknown_duration_files"] = sum(r.get("audio") is None for r in rows)
    write_json(output / "summary.json", summary)
    write_jsonl(output / "scores.jsonl", scores)
    overall = summary["overall"]
    (output / "summary.md").write_text(
        f"# 구간 정책 파일 평가\n\n조건: {summary['segmented_accuracy']['condition']}\n\n"
        f"성공/계획: {overall['successful']}/{overall['planned']}, 실패/미실행: {overall['failed']}/{overall['not_attempted']}\n\n"
        f"CER: {overall['cer']['value']}, 파일 파이프라인 RTF: {overall['rtf']}\n\n"
        + summary["segmented_accuracy"]["timing_scope"]
        + "\n\n구간을 생략해도 정답은 원본 발화 전체를 유지합니다. 빈 검출은 빈 가설의 삭제 오류로 채점합니다. 실시간 자막/endpoint/Refine 지연/드롭/백로그는 미측정입니다.\n",
        encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summarize(args.predictions, args.output)


if __name__ == "__main__":
    main()
