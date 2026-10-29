"""실제 구간 기록의 입력·payload·상한·큐 계측을 재검증하고 개발 결과를 요약한다."""

import argparse
import hashlib
import json
from array import array
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.audio_policy import preprocess
from edge_asr.dataset import load_audio, pcm_bytes
from edge_asr.metrics import distribution
from edge_asr.segmentation import validate_capture
from edge_asr.streaming import backlog_series, read_events
from edge_asr.vad import MODEL_SHA
from edge_asr.vad_probe import source_metrics


def verify_row(row, expected, pcm, policy, paced):
    meta, maximum = row["pipeline"], row["hard_limit_s"] * 16000
    validate_capture(meta, row["replay"] if paced else None, expected, policy)
    if row["audio_sha256"] != expected["audio_sha256"] or row["condition"] != expected["condition"]:
        raise ValueError("원본/조건 불일치")
    if meta["processed_pcm_sha256"] != hashlib.sha256(pcm_bytes(pcm)).hexdigest():
        raise ValueError("실제 공통 PCM 불일치")
    if (
        meta["owned_max_frames"] != maximum
        or meta["context_frames"] != 3200
        or meta["ring_capacity_frames"] != maximum + 3200 + 16000
        or meta["ring_peak_frames"] > meta["ring_capacity_frames"]
        or meta["native_active_peak_frames"] > 30 * 16000
        or meta["native_budget_frames"] != 30 * 16000
        or meta["native_budget_exhausted"]
        or meta["pending_peak_frames"] > 2111
        or meta["payload_peak_frames"] > maximum + 3200
        or meta["native_accepted_frames"] != ((len(pcm) + 511) // 512) * 512
    ):
        raise ValueError("버퍼/프레임 상한 위반")
    last = 0
    for index, segment in enumerate(row["segments"]):
        start, end = segment["owned_start_frame"], segment["owned_end_frame"]
        context = segment["context_start_frame"]
        if (
            segment["segment_id"] != index
            or not last <= start < end <= len(pcm)
            or end - start > maximum
            or context != max(0, start - 3200)
            or segment["context_end_frame"] != end
            or not end
            <= segment["emitted_at_audio_frame"]
            <= segment["available_input_frame"]
            <= len(pcm)
            or segment["payload_frames"] != end - context
            or segment["reason"] not in ("hard_limit", "vad_end", "eof")
        ):
            raise ValueError("소유 구간/문맥/시각/ID 불일치")
        for key, a in (("payload_sha256", context), ("owned_pcm_sha256", start)):
            if segment[key] != hashlib.sha256(pcm_bytes(pcm[a:end])).hexdigest():
                raise ValueError("구간 payload의 실제 PCM 불일치")
        if segment["after_flush"] != (segment["reason"] == "eof") or segment[
            "endpoint_eligible"
        ] != (segment["reason"] == "vad_end"):
            raise ValueError("자연 종료/강제 분할/EOF 구분 오류")
        if paced:
            wall = segment["emitted_monotonic_s"] - row["replay"]["t0_monotonic_ns"] / 1e9
            if segment["emitted_at_wall_s"] != wall or wall < end / 16000:
                raise ValueError("도착 전 반환 또는 실제 시계 불일치")
        elif segment["emitted_at_wall_s"] is not None:
            raise ValueError("빠른 입력에 실시간 지연을 기록할 수 없습니다.")
        last = end
    if meta["segments"] != len(row["segments"]) or meta["hard_limit_emissions"] != sum(
        s["reason"] == "hard_limit" for s in row["segments"]
    ):
        raise ValueError("반환 구간/강제 분할 수 불일치")
    metrics = source_metrics(
        row["segments"],
        8000,
        8000 + expected["source_frames"],
        expected_speech=bool(expected["reference"].strip()),
    )
    if any(row[k] != v for k, v in metrics.items()):
        raise ValueError("누락/오탐/대리 지연 집계 불일치")


def summarize(root, base, data):
    result = {
        "status": "mechanical_checks_passed_policy_not_approved",
        "runs": {},
        "vad_input_policy_approved": None,
        "actual_acoustic_endpoint_s": None,
        "asr_first_partial_s": None,
        "refine_finalization_s": None,
        "scope": "개발 자료의 1회 설정별 검사. 자연 단어/음소 정렬·ASR·마이크·열평형·최종 정확도 평가 아님.",
    }
    spans = json.loads((data / "component-spans.json").read_text())
    cache = {}
    for paced in (False, True):
        manifest = data / ("paced.jsonl" if paced else "all.jsonl")
        expected = {r["id"]: r for r in (json.loads(l) for l in manifest.read_text().splitlines())}
        for policy in ("identity", "rms26"):
            name = ("paced-" if paced else "unpaced-") + policy
            folder = base / name
            ex = json.loads((folder / "execution.json").read_text())
            w = json.loads((folder / "worker.json").read_text())
            maxima = [5] if paced else [3, 5, 8]
            if not (
                ex["control_valid"]
                and ex["limit_verified"]
                and ex["exit_code"] == 0
                and w["status"] == "completed"
            ):
                raise ValueError(f"실행/통제 실패: {name}")
            fixed = {
                "policy": policy,
                "paced": paced,
                "hard_limits_s": maxima,
                "sample_rate": 16000,
                "vad_window_frames": 512,
                "num_threads": 1,
                "threshold": 0.5,
                "min_speech_s": 0.1,
                "min_silence_s": 0.3,
                "native_soft_limit_s": 5,
                "prefix_frames": 8000,
                "suffix_frames": 16000,
                "context_frames": 3200,
                "native_buffer_s": 31,
                "native_active_budget_s": 30,
            }
            if (
                any(w["settings"].get(k) != v for k, v in fixed.items())
                or w["model_sha256"] != MODEL_SHA
                or w["input_manifest_sha256"] != digest(manifest)
            ):
                raise ValueError("사전 조건/모델/입력 목록 불일치")
            rows = [json.loads(l) for l in (folder / "samples.jsonl").read_text().splitlines()]
            seen = set()
            for row in rows:
                key = (row["hard_limit_s"], row["id"])
                if key in seen or key[0] not in maxima or key[1] not in expected:
                    raise ValueError("중복/예상 밖 기록")
                seen.add(key)
                e = expected[key[1]]
                cache_key = (policy, e["audio_sha256"])
                if cache_key not in cache:
                    audio = load_audio(root / e["audio_path"], 16000)
                    if audio.sha256 != e["audio_sha256"]:
                        raise ValueError("현재 원본 변경")
                    cache[cache_key] = preprocess(
                        array("f", [0] * 8000) + audio.samples + array("f", [0] * 16000), policy
                    )[0]
                verify_row(row, e, cache[cache_key], policy, paced)
                if paced:
                    t = row["replay"]
                    events = folder / "events" / f"{row['id']}-{row['hard_limit_s']}"
                    produced = read_events(events / "producer.jsonl")
                    consumed = [
                        r for r in read_events(events / "decoder.jsonl") if r["type"] == "dequeue"
                    ]
                    _, backlog = backlog_series(
                        produced, consumed, 16000, t["replay_elapsed_s"], e["padded_frames"] / 16000
                    )
                    captures = [r for r in produced if r["type"] in ("enqueue", "drop")]
                    if (
                        backlog != t["backlog"]
                        or any(r["type"] == "drop" for r in captures)
                        or len(captures) != len(consumed)
                        or len(captures) != (e["padded_frames"] + 1599) // 1600
                        or not produced[-1].get("complete")
                        or t["producer_exit_code"] != 0
                    ):
                        raise ValueError("독립 입력/소비/백로그 원시 이벤트 불일치")
            if seen != {(m, i) for m in maxima for i in expected}:
                raise ValueError("계획된 평가 행 누락")
            therm = [json.loads(l) for l in (folder / "thermal.jsonl").read_text().splitlines()]
            entry = {
                "source_commit": ex["source_commit"],
                "rows": len(rows),
                "loading_s": w["loading_s"],
                "settings": w["settings"],
                "start_temperature_c": ex["before"]["temperature_mc"] / 1000,
                "max_temperature_c": max(t["temperature_mc"] for t in therm) / 1000,
                "current_limit_values": sorted({t["current_limits"] for t in therm}),
                "history_limit_values": sorted({t["history_limits"] for t in therm}),
                "sample_file_sha256": digest(folder / "samples.jsonl"),
                "candidates": {},
            }
            for cap in maxima:
                group = [r for r in rows if r["hard_limit_s"] == cap]
                segments = [s for r in group for s in r["segments"]]
                positive = [r for r in group if r["expected_speech"]]
                negative = [r for r in group if not r["expected_speech"]]
                components = []
                for row in group:
                    for span in spans.get(row["id"], []):
                        metrics = source_metrics(
                            row["segments"],
                            8000 + span["start_frame"],
                            8000 + span["end_frame"],
                            expected_speech=True,
                        )
                        components.append(
                            {
                                "id": span["id"],
                                "whole_clip_missed": metrics["speech_clip_fully_missed"],
                                "wav_end_wall_proxy_s": metrics[
                                    "endpoint_from_source_wav_end_wall_s"
                                ],
                            }
                        )
                pipeline_seconds = sum(
                    r["replay"]["processing_s"] if paced else r["call_wall_s"] for r in group
                )
                entry["candidates"][str(cap)] = {
                    "files": len(group),
                    "speech_files": len(positive),
                    "whole_speech_files_missed": [
                        r["id"] for r in positive if r["speech_clip_fully_missed"]
                    ],
                    "negative_files": len(negative),
                    "negative_trigger_ids": [
                        r["id"] for r in negative if r["silence_or_noise_triggered"]
                    ],
                    "continuous_components": components,
                    "segments": len(segments),
                    "hard_limit_segments": sum(s["reason"] == "hard_limit" for s in segments),
                    "max_owned_s": max(
                        ((s["owned_end_frame"] - s["owned_start_frame"]) / 16000 for s in segments),
                        default=None,
                    ),
                    "max_payload_s": max(
                        (s["payload_frames"] / 16000 for s in segments), default=None
                    ),
                    "max_ring_frames": max(r["pipeline"]["ring_peak_frames"] for r in group),
                    "max_native_active_frames": max(
                        r["pipeline"]["native_active_peak_frames"] for r in group
                    ),
                    "committed_tail_overhang_frames": sum(
                        r["pipeline"]["committed_tail_overhang_frames"] for r in group
                    ),
                    "compute_rtf_padded_audio": pipeline_seconds
                    / sum(r["pipeline"]["frames"] / 16000 for r in group),
                    "parent_cpu_percent_over_call": sum(r["process_cpu_s"] for r in group)
                    / sum(r["call_wall_s"] for r in group)
                    * 100,
                    "parent_peak_rss_mib": max(
                        r["resources"]["rss_observed_max_mib"] for r in group
                    ),
                    "endpoint_wav_end_wall_proxy_s": distribution(
                        [
                            r["endpoint_from_source_wav_end_wall_s"]
                            for r in positive
                            if r["endpoint_from_source_wav_end_wall_s"] is not None
                        ]
                    ),
                    "hard_boundary_publish_delay_s": distribution(
                        [
                            s["emitted_at_wall_s"] - s["owned_end_frame"] / 16000
                            for s in segments
                            if s["reason"] == "hard_limit" and s["emitted_at_wall_s"] is not None
                        ]
                    ),
                    "dropped_chunks": sum(r["replay"]["dropped_chunks"] for r in group)
                    if paced
                    else None,
                    "max_queued_audio_s": max(
                        r["replay"]["backlog"]["event_max_audio_s"] for r in group
                    )
                    if paced
                    else None,
                    "queue_wait_s": distribution(
                        [v for r in group for v in r["replay"]["queue_wait_s_values"]]
                    )
                    if paced
                    else None,
                    "capture_lag_s": distribution(
                        [v for r in group for v in r["replay"]["capture_lag_s_values"]]
                    )
                    if paced
                    else None,
                    "producer_peak_rss_mib": max(
                        r["replay"]["producer_lifetime_peak_rss_mib"] for r in group
                    )
                    if paced
                    else None,
                    "producer_cpu_s": sum(r["replay"]["producer_cpu_s"] for r in group)
                    if paced
                    else None,
                }
            result["runs"][name] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument(
        "--data", type=Path, default=Path("data/evaluation/m3-segments-20261029-01")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = summarize(args.root, args.base, args.data)
    with args.output.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("검증한 행:", sum(r["rows"] for r in value["runs"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
