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
            if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row.get("schema_version") != 1 or row.get("mode") != "batch":
                raise ValueError("schema_version=1, mode=batch인 평가 결과가 필요합니다.")
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
            rows.append(row)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    if not rows:
        raise ValueError("인식 결과가 비어 있습니다.")
    return rows


def score_records(rows: list[dict], policy: str, references: dict[str, str] | None = None) -> tuple[dict, list[dict]]:
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
        return {
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

    summary = {"schema_version": 1, "mode": "batch", "normalization": policy,
               "unicode_database": unicodedata.unidata_version,
               "overall": aggregate(scores),
               "by_split": {label: aggregate([row for row in scores if row["split"] == label]) for label in sorted({row["split"] for row in scores})},
               "by_condition": {label: aggregate([row for row in scores if row["condition"] == label]) for label in sorted({row["condition"] for row in scores})},
               "unmeasured": {key: None for key in ("first_partial_from_audio_start_s", "aligned_recognition_latency_s",
                                                     "finalization_from_audio_end_s", "drops", "backlog")}}
    return summary, scores


def save_scores(output: Path, rows: list[dict], policy: str, references=None) -> dict:
    summary, scores = score_records(rows, policy, references)
    write_json(output / "summary.json", summary)
    write_jsonl(output / "scores.jsonl", scores)
    overall = summary["overall"]

    def fmt(value, percent=False):
        return "—" if value is None else f"{value * 100:.2f}%" if percent else f"{value:.4f}"

    lines = ["# 공통 파일 평가 요약", "",
             f"모드: batch / 정규화: {policy}", "",
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
             "모델 로딩·WAV 읽기/변환·워밍업은 RTF에서 제외하고 새 스트림 생성·특징 추출·디코딩·flush·최종 가설 반환을 포함합니다.",
             "CER은 NFC 기준이며 기본 ko_cer_v1은 공백·문장부호를 제거합니다. 빈 정답은 corpus CER에서 제외하고 별도 집계합니다.",
             "이 결과는 입력 대기가 없는 파일 평가입니다. 부분 자막·실제 인식 지연·드롭·백로그를 측정한 결과가 아닙니다.", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return summary
