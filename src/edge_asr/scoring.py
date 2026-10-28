"""저장된 가설을 모델·오디오 없이 재채점하고 같은 형식의 보고서를 만든다."""

import json
import math
from pathlib import Path
import unicodedata

from edge_asr.benchmark import distribution, write_json
from edge_asr.metrics import character_errors


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def read_predictions(path: Path) -> list[dict]:
    rows, ids = [], set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row.get("schema_version") != 1 or row.get("mode") not in {"batch", "paced_stream"}:
                raise ValueError("schema_version=1, mode=batch/paced_stream인 평가 결과가 필요합니다.")
            for key in ("id", "reference", "audio_path", "split", "condition"):
                if not isinstance(row.get(key), str):
                    raise ValueError(f"{key} 문자열이 필요합니다.")
            if not row["id"].strip() or row["id"] in ids:
                raise ValueError("비어 있거나 중복된 id입니다.")
            ids.add(row["id"])
            if row.get("status") not in {"success", "failed", "not_attempted", "interrupted"}:
                raise ValueError("알 수 없는 실행 상태입니다.")
            if row["status"] == "success":
                if not isinstance(row.get("hypothesis"), str):
                    raise ValueError("성공 결과에는 hypothesis 문자열이 필요합니다.")
                for value in (row.get("elapsed_s"), (row.get("audio") or {}).get("duration_s")):
                    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                        raise ValueError("성공 결과의 처리 시간·오디오 길이는 유한한 양수여야 합니다.")
                value = row.get("process_cpu_s")
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("CPU 시간은 유한한 0 이상 수여야 합니다.")
                value = (row.get("resources") or {}).get("rss_observed_max_mib")
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError("RSS 값은 유한한 0 이상 수 또는 null이어야 합니다.")
                if row["mode"] == "paced_stream":
                    from edge_asr.streaming import validate_telemetry
                    validate_telemetry(row.get("streaming"), row)
            rows.append(row)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    if not rows:
        raise ValueError("인식 결과가 비어 있습니다.")
    if len({row["mode"] for row in rows}) != 1:
        raise ValueError("batch와 paced_stream 결과를 한 집계로 섞지 않습니다.")
    return rows


def score_records(rows: list[dict], policy: str, references: dict[str, str] | None = None) -> tuple[dict, list[dict]]:
    modes = {row["mode"] for row in rows}
    if len(modes) != 1 or not modes <= {"batch", "paced_stream"}:
        raise ValueError("같은 평가 모드의 결과만 집계합니다.")
    mode = next(iter(modes))
    if references is not None and set(references) != {row["id"] for row in rows}:
        raise ValueError("교체 정답 목록과 인식 결과의 id 집합이 같아야 합니다.")
    scores = []
    for row in rows:
        reference = row["reference"] if references is None else references[row["id"]]
        score = {"id": row["id"], "status": row["status"], "split": row["split"],
                 "condition": row["condition"], "reference": reference, "error": row.get("error")}
        if row["status"] == "success":
            score.update(character_errors(reference, row["hypothesis"], policy))
            score.update(hypothesis=row["hypothesis"], elapsed_s=row["elapsed_s"],
                         duration_s=row["audio"]["duration_s"],
                         process_cpu_s=row["process_cpu_s"],
                         rtf=row["elapsed_s"] / row["audio"]["duration_s"],
                         rss_observed_max_mib=(row.get("resources") or {}).get("rss_observed_max_mib"))
        else:
            score["reference_characters"] = character_errors(reference, "", policy)["reference_characters"]
        if mode == "paced_stream":
            score["streaming"] = row.get("streaming")
            if row["status"] == "success":
                from edge_asr.streaming import validate_telemetry
                validate_telemetry(score["streaming"], row)
        scores.append(score)

    def aggregate(items):
        successful = [row for row in items if row["status"] == "success"]
        nonempty = [row for row in successful if row["reference_characters"] > 0]
        characters = sum(row["reference_characters"] for row in nonempty)
        all_characters = sum(row["reference_characters"] for row in items)
        edits = {key: sum(row[key] for row in nonempty)
                 for key in ("errors", "substitutions", "deletions", "insertions")}
        elapsed = sum(row["elapsed_s"] for row in successful)
        duration = sum(row["duration_s"] for row in successful)
        cpu = sum(row["process_cpu_s"] for row in successful)
        result = {
            "planned": len(items), "successful": len(successful),
            "failed": sum(row["status"] == "failed" for row in items),
            "not_attempted": sum(row["status"] == "not_attempted" for row in items),
            "interrupted": sum(row["status"] == "interrupted" for row in items),
            "complete": len(successful) == len(items),
            "reference_character_coverage": characters / all_characters if all_characters else None,
            "cer": {"value": edits["errors"] / characters if characters else None,
                    "reference_characters": characters, **edits,
                    "scope": "successful_nonempty_references",
                    "empty_reference_samples": sum(row["reference_characters"] == 0 for row in successful),
                    "empty_reference_nonempty_hypotheses": sum(not row["normalized_reference"] and bool(row["normalized_hypothesis"]) for row in successful)},
            "rtf": elapsed / duration if duration else None,
            "total_audio_s": duration, "total_inference_wall_s": elapsed,
            "mean_process_cpu_percent": cpu / elapsed * 100 if elapsed else None,
            "file_processing_s": distribution([row["elapsed_s"] for row in successful]),
            "file_rtf": distribution([row["rtf"] for row in successful]),
            "rss_observed_max_mib": max((row["rss_observed_max_mib"] for row in successful
                                          if row["rss_observed_max_mib"] is not None), default=None),
        }
        if mode == "paced_stream":
            from edge_asr.streaming import aggregate_streaming
            result["streaming"] = aggregate_streaming(items)
        return result

    summary = {"schema_version": 1, "mode": mode, "normalization": policy,
               "unicode_database": unicodedata.unidata_version,
               "overall": aggregate(scores),
               "by_split": {label: aggregate([row for row in scores if row["split"] == label]) for label in sorted({row["split"] for row in scores})},
               "by_condition": {label: aggregate([row for row in scores if row["condition"] == label]) for label in sorted({row["condition"] for row in scores})},
               "unmeasured": {key: None for key in ("first_partial_from_audio_start_s", "aligned_recognition_latency_s",
                                                     "finalization_from_audio_end_s", "drops", "backlog")}}
    if mode == "paced_stream":
        summary["unmeasured"] = {key: None for key in ("aligned_recognition_latency_s", "speech_endpoint_latency_s",
                                                      "microphone_overruns", "ui_display_latency_s")}
    return summary, scores


def save_scores(output: Path, rows: list[dict], policy: str, references=None) -> dict:
    summary, scores = score_records(rows, policy, references)
    write_json(output / "summary.json", summary)
    write_jsonl(output / "scores.jsonl", scores)
    overall = summary["overall"]

    def fmt(value, percent=False):
        return "—" if value is None else f"{value * 100:.2f}%" if percent else f"{value:.4f}"

    lines = ["# 공통 파일 평가 요약", "",
             f"모드: {summary['mode']} / 정규화: {policy}", "",
             f"성공/계획: {overall['successful']}/{overall['planned']}, 실패: {overall['failed']}, 미실행: {overall['not_attempted']}, 중단: {overall['interrupted']}",
             f"평가 완료 여부: {overall['complete']}. 실패를 제외한 CER이므로 성공률·정답 문자 커버리지를 함께 확인하세요.", "",
             "| 항목 | 값 |", "| --- | --- |",
             f"| Corpus CER | {fmt(overall['cer']['value'], True)} |",
             f"| 정답 문자 커버리지 | {fmt(overall['reference_character_coverage'], True)} |",
             f"| 총 오디오 (초, 성공 파일) | {fmt(overall['total_audio_s'])} |",
             f"| 총 추론 처리 (초, 성공 파일) | {fmt(overall['total_inference_wall_s'])} |",
             f"| Corpus RTF | {fmt(overall['rtf'])} |",
             f"| 추론 구간 평균 프로세스 CPU (%) | {fmt(overall['mean_process_cpu_percent'])} |",
             f"| 추론 구간 관측 최대 RSS (MiB) | {fmt(overall['rss_observed_max_mib'])} |", "",
             "모델 로딩·WAV 읽기/변환·워밍업은 RTF에서 제외하고 새 인식 상태·특징 추출·디코딩·모델별 종료 처리(해당 시 flush)·최종 텍스트 반환을 포함합니다.",
             "CER은 NFC 기준이며 기본 ko_cer_v1은 공백·문장부호를 제거합니다. 빈 정답은 corpus CER에서 제외하고 별도 집계합니다.",
             "이 결과는 입력 대기가 없는 파일 평가입니다. 부분 자막·실제 인식 지연·드롭·백로그를 측정한 결과가 아닙니다.", ""]
    if summary["mode"] == "paced_stream":
        stream = overall["streaming"]
        def pair(key):
            stats = stream[key]
            return f"{fmt(stats['p50'])}/{fmt(stats['p95'])} (n={stats['n']})"
        lines = lines[:-5] + [
            "| 오디오 시작→첫 비어 있지 않은 부분 반환 p50/p95 (초) | " + pair("first_partial_from_audio_start_s") + " |",
            "| 파일 입력 끝→최종 반환 p50/p95 (초) | " + pair("finalization_from_audio_end_s") + " |",
            "| 청크 처리 p50/p95 (초) | " + pair("chunk_processing_s") + " |",
            "| 큐 대기 p50/p95 (초) | " + pair("queue_wait_s") + " |",
            f"| 큐 백로그 이벤트 최대 (초) | {fmt(stream['backlog_event_max_audio_s'])} |",
            f"| 큐 백로그 격자 p95 (초) | {fmt(stream['backlog_audio_s']['p95'])} |",
            f"| 입력 큐 드롭 (청크/오디오 초) | {stream['dropped_chunks']}/{fmt(stream['dropped_audio_s'])} |",
            f"| 부분 가설 없는 성공 파일 | {stream['no_partial_files']} |",
            f"| replay 전체 경과/오디오 길이 | {fmt(stream['replay_elapsed_ratio'])} |",
            f"| replay 주 프로세스 평균 CPU (%) | {fmt(stream['mean_replay_process_cpu_percent'])} |", "",
            "RTF는 입력 대기를 뺀 스트림 생성·청크 전달/디코딩/결과 반환·최종 flush의 활성 처리 시간 합 / 실제 오디오 길이입니다.",
            "replay 전체 경과는 1배속 입력 대기를 포함합니다. 주 PID CPU/RSS와 입력 자식의 CPU·시작 포함 RSS 최고치는 구분합니다.",
            "첫 부분 시점은 음성이 쌓이는 대기·모델 lookahead를 포함하며 실제 인식 지연이 아닙니다. 최종 지연은 파일 끝 기준 강제 flush이며 음향적 발화 끝 endpoint가 아닙니다.",
            "큐 백로그는 처리 중 청크를 제외합니다. 실제 큐 이벤트를 100ms 격자로 재구성하고 순간 이벤트 최대도 별도 보고합니다.",
            "드롭은 모사 입력 큐의 drop_newest이며 마이크 하드웨어 손실은 미계측입니다. 성공 결과의 지표이며 실패·미실행을 함께 확인하세요.",
            "CER 정규화는 batch와 같습니다. 실제 마이크·자막 화면·정답 시각 정렬·장시간 서비스는 이번 모드에서 검증하지 않습니다.", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return summary
