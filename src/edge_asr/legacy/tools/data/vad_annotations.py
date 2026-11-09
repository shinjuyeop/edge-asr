"""원본을 보존하며 배포 음소 라벨의 예비 주석과 사전 고정한 화자 분할을 만든다."""

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.audio_policy import preprocess
from edge_asr.dataset import load_audio, pcm_bytes
from edge_asr.textgrid import read_textgrid
from edge_asr.vad_annotations import validate_annotation

HOLDOUT = [f"s{i:02}" for i in (5, 10, 15, 20, 25, 30, 35, 40)]
# 배포 논문 Table 3의 음소 표기. 미등록 표기는 ignore로 보존한다.
PHONES = set(
    "k0 kk kh t0 tt th p0 pp ph c0 cc ch s0 ss hh mm nn ng ll ii ee aa xx vv uu oo ye ya yv yu yo wi we wa wv xi".split()
)


def project_regions(intervals, start, end):
    regions, cursor = [], 0
    for interval in intervals:
        a, b = (
            max(start, round(interval.start * 16000)) - start,
            min(end, round(interval.end * 16000)) - start,
        )
        if a >= b:
            continue
        if a < cursor:
            raise ValueError("변환 후 음소 구간 겹침")
        if a > cursor:
            regions.append(
                {
                    "start_frame": cursor,
                    "end_frame": a,
                    "label": "ignore",
                    "reason": "uncovered_by_source_label",
                }
            )
        label = (
            "speech"
            if interval.text in PHONES
            else "non_speech"
            if interval.text == "<SIL>"
            else "ignore"
        )
        regions.append(
            {
                "start_frame": a,
                "end_frame": b,
                "label": label,
                "reason": "corpus_phoneme:" + interval.text,
            }
        )
        cursor = b
    if cursor < end - start:
        regions.append(
            {
                "start_frame": cursor,
                "end_frame": end - start,
                "label": "ignore",
                "reason": "uncovered_by_source_label",
            }
        )
    return regions


def prepare(root, packet, labels, output):
    root, packet, labels, output = (p.resolve() for p in (root, packet, labels, output))
    if (
        any(not p.is_relative_to(root / "data") for p in (packet, labels, output))
        or output == root / "data"
    ):
        raise ValueError("data/ 아래의 별도 경로가 필요합니다.")
    if output.exists():
        raise FileExistsError("기존 주석을 덮어쓰지 않습니다.")
    receipt = json.loads((packet / "provenance.json").read_text())
    if (
        receipt["status"] != "human_review_required"
        or digest(packet / "review.jsonl") != receipt["files"]["review.jsonl"]
    ):
        raise ValueError("검수 패킷 목록 변경/미완성")
    reviews = [json.loads(line) for line in (packet / "review.jsonl").read_text().splitlines()]
    rows, grids = [], {}
    with zipfile.ZipFile(labels) as z:
        for review in reviews:
            path = (root / review["audio_path"]).resolve()
            if not path.is_relative_to(packet / "audio") or digest(path) != review["audio_sha256"]:
                raise ValueError("검수 음원 경로·해시 불일치")
            audio = load_audio(path, 16000)
            n = len(audio.samples)
            row = {
                "schema": "vad-annotation-v1",
                "id": review["id"],
                "audio_path": review["audio_path"],
                "audio_sha256": audio.sha256,
                "frames": n,
                "sample_rate_hz": 16000,
                "category": review["category"],
                "speaker_id": review.get("speaker_id"),
                "split": "holdout" if review.get("speaker_id") in HOLDOUT else "development",
                "reviewer": None,
                "reviewed_at": None,
                "review_method": None,
                "review_note": None,
                "endpoint_reference_frame": None,
                "target_interval_frames": None,
            }
            if review["source"] == "seoul_corpus":
                name = review["label_member"]
                if name not in grids:
                    raw = z.read(name)
                    grids[name] = (hashlib.sha256(raw).hexdigest(), read_textgrid(raw))
                checksum, grid = grids[name]
                if checksum != review["label_sha256"]:
                    raise ValueError("음소 라벨 변경")
                start, end = review["source_clip_frames_16k"]
                if end - start != n:
                    raise ValueError("라벨/WAV 프레임 수 불일치")
                phones = [t for t in grid.tiers if t.name == "phoneme"]
                if len(phones) != 1:
                    raise ValueError("단일 음소 계층 필요")
                row.update(
                    annotation_status="corpus_provisional",
                    regions=project_regions(phones[0].intervals, start, end),
                    target_interval_frames=[
                        round(v * 16000) - start for v in review["source_utterance_s"]
                    ],
                    corpus_source={
                        "member": name,
                        "sha256": checksum,
                        "source_clip_frames_16k": [start, end],
                    },
                )
            else:
                row.update(
                    annotation_status="unreviewed",
                    regions=[
                        {
                            "start_frame": 0,
                            "end_frame": n,
                            "label": "ignore",
                            "reason": "noise_recording_not_listened_to",
                        }
                    ],
                )
            row["expected_processed"] = {}
            for policy in ("identity", "rms26"):
                pcm, info = preprocess(audio.samples, policy)
                row["expected_processed"][policy] = {
                    "pcm_sha256": hashlib.sha256(pcm_bytes(pcm)).hexdigest(),
                    "newly_clipped_samples": info["newly_clipped_samples"],
                }
            validate_annotation(row)
            rows.append(row)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("중복 후보 ID")
    output.mkdir(parents=True, exist_ok=False)
    (output / "annotations.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    plan = {
        "schema": "public-vad-provisional-v1",
        "source_packet_sha256": digest(packet / "provenance.json"),
        "source_review_sha256": digest(packet / "review.jsonl"),
        "source_label_archive_sha256": digest(labels),
        "holdout_speakers": HOLDOUT,
        "development_counts": dict(
            Counter(r["category"] for r in rows if r["split"] == "development")
        ),
        "holdout_counts": dict(Counter(r["category"] for r in rows if r["split"] == "holdout")),
        "label_policy": {
            "speech_phones": sorted(PHONES),
            "non_speech": ["<SIL>"],
            "otherwise": "ignore",
        },
        "trial": {
            "policies": ["identity", "rms26"],
            "hard_limit_s": 5,
            "native_soft_limit_s": 5,
            "threshold": 0.5,
            "num_threads": 1,
            "repetitions": 1,
            "warmups_per_policy": 1,
            "mode": "unpaced",
            "synthetic_padding_frames": 0,
        },
        "notes_ko": [
            "성능 확인 전에 화자 8명(18개)을 보류. 모델 학습 중복 없는 최종 독립 평가라고 주장하지 않음.",
            "배포 음소 라벨 기준 예비 합치율. 사람이 청취/파형 검수한 정답 정확도와 분리.",
            "잡음 5개는 전 구간 ignore. VAD 출력은 관측하되 오탐률을 계산하지 않음.",
            "각 WAV의 원래 문맥부터 전처리 상태 초기화. 연속 원본 전체 추론과 다름.",
            "Native 30초 예산/열 통제 실패는 별도 실패로 보존. 실제 endpoint와 ASR 지연은 미측정.",
        ],
        "files": {"annotations.jsonl": digest(output / "annotations.jsonl")},
    }
    (output / "provenance.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument(
        "--packet", type=Path, default=Path("data/review/m3-public-vad-audio-20261029-01")
    )
    p.add_argument(
        "--labels",
        type=Path,
        default=Path("data/sources/seoul-corpus-20261029-01/label-TextGrid.zip"),
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(
        json.dumps(
            prepare(a.root, a.root / a.packet, a.root / a.labels, a.root / a.output),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
