"""실제 VAD 기록을 검증·집계한다. 오탐/미검증을 숨기거나 정책을 자동 채택하지 않는다."""

import argparse
import json
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.metrics import distribution
from edge_asr.vad_probe import source_metrics


def aggregate(rows):
    speech = [r for r in rows if r["expected_speech"]]
    negative = [r for r in rows if not r["expected_speech"]]
    segments = [s for r in rows for s in r["segments"]]
    wall = sum(r["vad_call_wall_s"] for r in rows)
    fed = sum(r["timing"]["native_accepted_frames"] for r in rows) / 16000
    return {
        "files": len(rows),
        "speech_files": len(speech),
        "speech_clips_fully_missed": sum(r["speech_clip_fully_missed"] for r in speech),
        "negative_files": len(negative),
        "negative_files_triggered": sum(r["silence_or_noise_triggered"] for r in negative),
        "segments": len(segments),
        "max_segment_s": max(
            ((s["raw_end_frame"] - s["raw_start_frame"]) / 16000 for s in segments), default=None
        ),
        "segments_exceeding_soft_max": sum(r["segments_exceeding_soft_max"] for r in rows),
        "endpoint_wav_end_audio_proxy_s": distribution(
            [
                r["endpoint_from_source_wav_end_audio_s"]
                for r in speech
                if r["endpoint_from_source_wav_end_audio_s"] is not None
            ]
        ),
        "endpoint_wav_end_wall_proxy_s": distribution(
            [
                r["endpoint_from_source_wav_end_wall_s"]
                for r in speech
                if r["endpoint_from_source_wav_end_wall_s"] is not None
            ]
        ),
        "processing_rtf_including_padded_audio": sum(r["timing"]["processing_wall_s"] for r in rows)
        / fed
        if fed
        else None,
        "process_cpu_percent_over_call_wall": sum(r["vad_call_cpu_s"] for r in rows) / wall * 100
        if wall
        else None,
        "observed_peak_rss_mib": max(
            (
                r["resources"]["rss_observed_max_mib"]
                for r in rows
                if r["resources"]["rss_observed_max_mib"] is not None
            ),
            default=None,
        ),
        "paced_input_lateness_s": distribution(
            [v for r in rows for v in r["timing"]["input_lateness_s"]]
        ),
        "actual_acoustic_endpoint_s": None,
        "input_drops": None,
        "queue_backlog": None,
    }


def summarize(base, data):
    result = {
        "runs": {},
        "vad_input_policy_approved": None,
        "scope": "개발 자료. 같은 파일을 후보마다 반복. WAV 범위 기준 전체 누락/대리 지연이며 단어 정확도·음소 정렬·독립 최종 평가 아님.",
    }
    spans = json.loads((data / "component-spans.json").read_text())
    model_hash = None
    for paced in (False, True):
        for policy in ("identity", "rms26"):
            name = ("paced-" if paced else "unpaced-") + policy
            folder = base / name
            receipt = json.loads((folder / "execution.json").read_text())
            worker = json.loads((folder / "worker.json").read_text())
            if not receipt["control_valid"] or worker["status"] != "completed":
                raise ValueError(f"통제/실행 미완료: {name}")
            settings = worker["settings"]
            fixed = {
                "sample_rate": 16000,
                "num_threads": 1,
                "window_size": 512,
                "threshold": 0.5,
                "min_speech_duration": 0.1,
                "min_silence_duration": 0.3,
            }
            if any(settings.get(k) != v for k, v in fixed.items()):
                raise ValueError("사전 고정 VAD 설정 불일치")
            if model_hash is not None and model_hash != worker["model_sha256"]:
                raise ValueError("VAD 모델 해시 불일치")
            model_hash = worker["model_sha256"]
            manifest = data / f"{name if paced else policy}.jsonl"
            if digest(manifest) != worker["input_manifest_sha256"]:
                raise ValueError("manifest 해시 불일치")
            expected = {
                r["id"]: r for r in (json.loads(line) for line in manifest.read_text().splitlines())
            }
            rows = [
                json.loads(line) for line in (folder / "segments.jsonl").read_text().splitlines()
            ]
            maxima = worker["settings"]["max_speech_duration_candidates"]
            seen = set()
            for row in rows:
                key = row["max_segment_s"], row["id"]
                if key in seen or key[0] not in maxima or key[1] not in expected:
                    raise ValueError("중복/예상하지 않은 평가 행")
                seen.add(key)
                e = expected[key[1]]
                if row["audio_sha256"] != e["audio_sha256"] or row["condition"] != e["condition"]:
                    raise ValueError("입력/조건 불일치")
                if row["expected_speech"] != bool(e["reference"].strip()):
                    raise ValueError("음성/무음 라벨 불일치")
                if row["timing"]["paced"] != paced:
                    raise ValueError("입력 시간 모드 불일치")
            if seen != {(m, i) for m in maxima for i in expected}:
                raise ValueError("누락된 평가 행")
            thermal = [json.loads(s) for s in (folder / "thermal.jsonl").read_text().splitlines()]
            entry = {
                "source_commit": receipt["source_commit"],
                "model_sha256": worker["model_sha256"],
                "settings": worker["settings"],
                "total_rows": len(rows),
                "start_temperature_c": receipt["before"]["temperature_mc"] / 1000,
                "max_temperature_c": max(r["temperature_mc"] for r in thermal) / 1000,
                "current_limit_values": sorted(set(r["current_limits"] for r in thermal)),
                "history_limit_values": sorted(set(r["history_limits"] for r in thermal)),
                "segment_file_sha256": digest(folder / "segments.jsonl"),
                "candidates": {},
            }
            for maximum in maxima:
                group = [r for r in rows if r["max_segment_s"] == maximum]
                conditions = {}
                for condition in sorted({r["condition"] for r in group}):
                    a = aggregate([r for r in group if r["condition"] == condition])
                    conditions[condition] = {
                        k: a[k]
                        for k in (
                            "files",
                            "speech_files",
                            "speech_clips_fully_missed",
                            "negative_files",
                            "negative_files_triggered",
                            "segments",
                            "max_segment_s",
                            "segments_exceeding_soft_max",
                        )
                    }
                component_checks = []
                for row in group:
                    for span in spans.get(row["id"], []):
                        m = source_metrics(
                            row["segments"],
                            8000 + span["start_frame"],
                            8000 + span["end_frame"],
                            expected_speech=True,
                        )
                        component_checks.append(
                            {"id": span["id"], "whole_clip_missed": m["speech_clip_fully_missed"]}
                        )
                entry["candidates"][str(maximum)] = {
                    "overall": aggregate(group),
                    "by_condition": conditions,
                    "continuous_components": component_checks,
                    "negative_trigger_ids": [
                        r["id"] for r in group if r["silence_or_noise_triggered"]
                    ],
                }
            result["runs"][name] = entry
    result["decision"] = (
        "전체 누락·음성 아닌 입력의 오탐·hard 상한·정렬 라벨을 검토한 뒤 채택. 검출 결과만으로 안전한 Refine 호출을 보장하지 않음."
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data/evaluation/m3-vad-20261029-01"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = summarize(args.base, args.data)
    with args.output.open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print("검증한 실행:", len(value["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
