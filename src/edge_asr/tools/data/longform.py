"""로컬 공개 원본으로 약 1분 자연발화·낭독 연결 개발 세트를 준비한다. ASR 없음."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import wave
import zipfile
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json, write_jsonl
from edge_asr.dataset import load_audio, load_manifest, write_wav
from edge_asr.metrics import normalize
from edge_asr.textgrid import read_textgrid
from edge_asr.tools.data import zeroth
from edge_asr.tools.data.external import convert, finish, verify_existing

OUTPUT = Path("data/evaluation/longform-20261117-01")
SEOUL = Path("data/sources/seoul-corpus-20261029-01")
ZEROTH = Path("data/sources/edge100-20261109-01")
ARCHIVES = {
    "label-TextGrid.zip": "dab4d35509755c6cfee7c118c2d8868aa1dddfb0367e7b50b107e5fc9a464275",
    "sound-flac.zip": "2ef8f19ee60c0710d97c36c0ee616c265097f670c495d36afccc26b6412eb4d4",
}
NONLEXICAL = {"", "<SIL>", "<VOCNOISE>", "<LAUGH>", "<NOISE>"}
QUOTAS = (
    (10, "male", "dense"),
    (10, "female", "hesitation"),
    (20, "male", "pauses"),
    (20, "female", "laughter"),
    (30, "male", "hesitation"),
    (30, "female", "dense"),
    (40, "male", "laughter"),
    (40, "female", "pauses"),
)
RECIPE = {
    "name": "longform-development-v1",
    "seed": "edge-asr-longform-20261117-v1",
    "natural_duration_s": [50, 75],
    "natural_quotas": [list(q) for q in QUOTAS],
    "read_quotas": [[gender, gap] for gender in ("male", "female") for gap in (0, 1.2)],
    "read_target_s": 60,
    "read_max_s": 80,
    "reference": "Seoul utt.ortho. / Zeroth official transcripts, ko_cer_v1",
    "natural_policy": "단일 연속 구간, 양끝 발화 라벨 경계, 내부 원음/무음 보존",
    "excluded_labels": "허용한 비언어 라벨 외 모든 태그 포함 구간 제외; 면접자 전사 추정 없음",
    "role": "development_regression_not_independent_holdout",
    "human_audio_review": False,
}


def label_kind(text):
    if text in NONLEXICAL:
        return "nonlexical"
    return "excluded" if "<" in text or ">" in text else "speech"


def natural_candidates(name, intervals):
    """태그를 삭제해 불완전한 전사를 만들지 않고, 해당 구간 전체를 선정에서 제외한다."""
    match = re.fullmatch(r"(s\d{2})([mf])(\d{2})[mf]\d.TextGrid", name)
    if match is None:
        raise ValueError("Seoul 원본 파일명이 필요합니다.")
    speaker, gender, age = match.groups()
    for i, first in enumerate(intervals):
        if label_kind(first.text) != "speech":
            continue
        endings = []
        for j in range(i, len(intervals)):
            last = intervals[j]
            if label_kind(last.text) == "excluded" or last.end - first.start > 75:
                break
            if last.end - first.start >= 50 and label_kind(last.text) == "speech":
                endings.append(j)
        if not endings:
            continue
        j = min(endings, key=lambda j: (abs(intervals[j].end - first.start - 60), j))
        window = intervals[i : j + 1]
        text = " ".join(p.text.strip() for p in window if label_kind(p.text) == "speech")
        duration = window[-1].end - first.start
        silence = [p.end - p.start for p in window if p.text == "<SIL>"]
        spoken_s = sum(p.end - p.start for p in window if label_kind(p.text) == "speech")
        yield {
            "id": f"seoul-{Path(name).stem}-u{i + 1:04d}-{j + 1:04d}",
            "label_member": name,
            "speaker_id": speaker,
            "gender": {"m": "male", "f": "female"}[gender],
            "age_group": int(age) // 10 * 10,
            "source_start_s": first.start,
            "source_end_s": window[-1].end,
            "reference": text,
            "source_interval_indices": [i + 1, j + 1],
            "label_metrics": {
                "duration_s": duration,
                "speech_ratio": spoken_s / duration,
                "silence_s": sum(silence),
                "long_pauses": sum(s >= 0.5 for s in silence),
                "filled_pause_tokens": sum(
                    w in {"어", "음", "아", "그", "뭐"} for w in text.split()
                ),
                "laughter_s": sum(p.end - p.start for p in window if p.text == "<LAUGH>"),
            },
        }


def rank(row, style):
    m = row["label_metrics"]
    value = {
        "dense": m["speech_ratio"],
        "pauses": m["long_pauses"],
        "hesitation": m["filled_pause_tokens"],
        "laughter": m["laughter_s"],
    }[style]
    tie = hashlib.sha256((RECIPE["seed"] + "/" + row["id"]).encode()).hexdigest()
    return (-value, abs(m["duration_s"] - 60), tie)


def select_natural(candidates):
    chosen = []
    for age, gender, style in QUOTAS:
        pool = [r for r in candidates if (r["age_group"], r["gender"]) == (age, gender)]
        if not pool:
            raise ValueError(f"자연발화 할당량 부족: {age}/{gender}/{style}")
        row = min(pool, key=lambda r: rank(r, style))
        if style != "dense" and rank(row, style)[0] >= 0:
            raise ValueError(f"{style} 주석 특성이 있는 자연발화가 없습니다.")
        chosen.append({**row, "condition": "natural_" + style})
    return chosen


def scan_labels(archive):
    candidates, audit = [], []
    with zipfile.ZipFile(archive) as handle:
        names = sorted(
            n for n in handle.namelist() if re.fullmatch(r"s\d{2}[mf]\d{2}[mf]\d.TextGrid", n)
        )
        if len(names) != 240 or len(set(names)) != 240:
            raise ValueError("고정 Seoul TextGrid 240개가 필요합니다.")
        for name in names:
            blob = handle.read(name)
            try:
                grid = read_textgrid(blob)
                tiers = [t for t in grid.tiers if t.name == "utt.ortho."]
                if len(tiers) != 1:
                    raise ValueError("단일 utt.ortho. tier가 필요합니다.")
            except (ValueError, UnicodeError) as error:
                audit.append({"member": name, "status": "quarantined", "reason": str(error)})
                continue
            rows = list(natural_candidates(name, tiers[0].intervals))
            candidates.extend(rows)
            audit.append({"member": name, "status": "parsed", "candidates": len(rows)})
    return select_natural(candidates), audit


def read_pcm(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (
            16000,
            1,
            2,
            "NONE",
        ):
            raise ValueError("16kHz mono PCM16 WAV가 필요합니다.")
        pcm = wav.readframes(wav.getnframes())
        if len(pcm) != wav.getnframes() * 2:
            raise ValueError("WAV 프레임이 잘렸습니다.")
        return pcm


def crop(source, output, start, end):
    """전체 리샘플링 뒤 정수 프레임으로 한 번 절취한다. 내부 침묵·비언어음은 유지한다."""
    lo, hi = round(start * 16000), round(end * 16000)
    with wave.open(str(source), "rb") as wav:
        if not 0 <= lo < hi <= wav.getnframes():
            raise ValueError("절취 범위가 원음 밖입니다.")
        wav.setpos(lo)
        pcm = wav.readframes(hi - lo)
    if len(pcm) != (hi - lo) * 2:
        raise ValueError("절취 PCM이 잘렸습니다.")
    write_wav(output, pcm)
    return [lo, hi]


def audio_row(stage, relative, row):
    path = stage / "wav" / (row["id"] + ".wav")
    audio = load_audio(path, 16000)
    pcm = read_pcm(path)
    return {
        **row,
        "audio_path": (relative / "wav" / path.name).as_posix(),
        "audio_sha256": audio.sha256,
        "pcm16_sha256": hashlib.sha256(pcm).hexdigest(),
        "frames": len(audio.samples),
        "duration_s": audio.duration_s,
        "split": "development_regression",
        "reference_status": "source_transcript_not_independently_listened",
        "reference_normalized_chars": len(normalize(row["reference"])),
    }


def prepare_natural(root, stage, relative):
    source = root / SEOUL
    for name, sha in ARCHIVES.items():
        if digest(source / name) != sha:
            raise ValueError(f"Seoul 원본 SHA-256 불일치: {name}")
    selected, audit = scan_labels(source / "label-TextGrid.zip")
    write_jsonl(stage / "label-audit.jsonl", audit)
    rows = []
    with (
        zipfile.ZipFile(source / "sound-flac.zip") as sounds,
        zipfile.ZipFile(source / "label-TextGrid.zip") as labels,
    ):
        for row in selected:
            stem = Path(row["label_member"]).stem
            original = stage / "original" / (stem + ".flac")
            original.write_bytes(sounds.read(original.name))
            label = stage / "original" / row["label_member"]
            label.write_bytes(labels.read(label.name))
            full = stage / "full.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-xerror",
                    "-n",
                    "-i",
                    str(original),
                    "-map_metadata",
                    "-1",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(full),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            bounds = crop(
                full,
                stage / "wav" / (row["id"] + ".wav"),
                row["source_start_s"],
                row["source_end_s"],
            )
            full.unlink()
            rows.append(
                audio_row(
                    stage,
                    relative,
                    {
                        **row,
                        "source": "seoul_corpus",
                        "synthetic": False,
                        "source_pcm16_frames": bounds,
                        "original_sha256": digest(original),
                        "label_sha256": digest(label),
                        "license": "CC-BY-NC-2.0",
                    },
                )
            )
            print(f"자연발화 {row['condition']} / {row['speaker_id']}", flush=True)
    return rows


def select_read(candidates, excluded):
    selected, used = [], set()
    for gender, gap in RECIPE["read_quotas"]:
        pool = [
            r
            for r in candidates
            if r["gender"] == gender and normalize(r["reference"]) not in excluded
        ]
        speakers = sorted({r["speaker_id"] for r in pool} - used)
        if not speakers:
            raise ValueError("기존100개와 겹치지 않는 낭독 화자 부족")
        # 충분한 분량이 있는 첫 화자. 같은 원문을 반복해 목표 시간을 채우지 않는다.
        for speaker in speakers:
            sources, frames = [], 0
            for row in sorted(
                (r for r in pool if r["speaker_id"] == speaker), key=lambda r: r["id"]
            ):
                text = normalize(row["reference"])
                if text in {normalize(r["reference"]) for r in sources}:
                    continue
                frames += row["frames"] + (round(gap * 16000) if sources else 0)
                sources.append(row)
                if frames >= 60 * 16000:
                    break
            if 60 * 16000 <= frames <= 80 * 16000:
                used.add(speaker)
                selected.append((gender, gap, sources))
                break
        else:
            raise ValueError("중복 없는 60~80초 낭독 원음 부족")
    return selected


def prepare_read(root, stage, relative):
    cache = root / ZEROTH
    if digest(cache / "zeroth-prefix.bin") != zeroth.PREFIX_SOURCE[1]:
        raise ValueError("Zeroth 원본 prefix SHA-256 불일치")
    files = zeroth.members(cache)
    excluded = {
        normalize(r["reference"])
        for r in read_jsonl(root / "data/evaluation/edge100-20261109-01/manifest.jsonl")
    }
    selected = select_read(zeroth.candidates(files), excluded)
    rows = []
    for gender, gap, sources in selected:
        chunks, timeline, frames = [], [], 0
        for source in sources:
            if chunks:
                silence = b"\0\0" * round(gap * 16000)
                chunks.append(silence)
                frames += len(silence) // 2
            original = stage / "original" / (source["id"] + ".flac")
            original.write_bytes(files[source["member"]])
            converted = stage / "part.wav"
            convert(original, converted, source["frames"])
            pcm = read_pcm(converted)
            converted.unlink()
            chunks.append(pcm)
            timeline.append(
                {
                    **source,
                    "start_frame": frames,
                    "end_frame": frames + len(pcm) // 2,
                    "original_sha256": digest(original),
                    "pcm16_sha256": hashlib.sha256(pcm).hexdigest(),
                }
            )
            frames += len(pcm) // 2
        kind = "tight" if gap == 0 else "pauses"
        row = {
            "id": f"read-{gender}-{kind}",
            "source": "zeroth",
            "synthetic": True,
            "condition": "concatenated_read_" + kind,
            "gender": gender,
            "speaker_id": sources[0]["speaker_id"],
            "gap_s": gap,
            "reference": " ".join(r["reference"] for r in sources),
            "source_timeline": timeline,
            "license": "CC-BY-4.0",
        }
        write_wav(stage / "wav" / (row["id"] + ".wav"), b"".join(chunks))
        rows.append(audio_row(stage, relative, row))
    # 원본 전사와 화자 메타데이터도 보존한다.
    for name, blob in files.items():
        if name == "AUDIO_INFO" or name.endswith(".trans.txt"):
            (stage / "original" / Path(name).name).write_bytes(blob)
    return rows


def verify(root, output):
    info = verify_existing(output, RECIPE)
    rows = read_jsonl(output / "manifest.jsonl")
    samples, _ = load_manifest(output / "manifest.jsonl", root)
    if len(rows) != 12 or len({r["pcm16_sha256"] for r in rows}) != 12:
        raise ValueError("서로 다른 12개 음원이 필요합니다.")
    for name, synthetic in (("natural", False), ("reading", True)):
        if read_jsonl(output / (name + ".jsonl")) != [
            r for r in rows if r["synthetic"] == synthetic
        ]:
            raise ValueError("전체/부분 목록 불일치")
    for sample, row in zip(samples, rows, strict=True):
        audio = load_audio(sample.audio_path, 16000)
        if audio.sha256 != sample.audio_sha256 or len(audio.samples) != row["frames"]:
            raise ValueError("WAV 해시/프레임 불일치")
        if hashlib.sha256(read_pcm(sample.audio_path)).hexdigest() != row["pcm16_sha256"]:
            raise ValueError("PCM 해시 불일치")
        if not 50 <= audio.duration_s <= 80 or not normalize(sample.reference):
            raise ValueError("길이/정답 조건 불일치")
        verify_reference(row, read_pcm(sample.audio_path), output)
    return info


def verify_reference(row, pcm, output):
    if not row["synthetic"]:
        label = output / "original" / row["label_member"]
        grid = read_textgrid(label.read_bytes())
        (tier,) = [t for t in grid.tiers if t.name == "utt.ortho."]
        candidates = natural_candidates(row["label_member"], tier.intervals)
        expected = next((r for r in candidates if r["id"] == row["id"]), None)
        if expected is None or any(row[k] != expected[k] for k in expected):
            raise ValueError("원본 시간 라벨/전사와 자연발화 목록 불일치")
        bounds = [round(row[k] * 16000) for k in ("source_start_s", "source_end_s")]
        if bounds != row["source_pcm16_frames"] or len(pcm) != (bounds[1] - bounds[0]) * 2:
            raise ValueError("자연발화 절취 프레임 불일치")
    else:
        previous = 0
        for index, source in enumerate(row["source_timeline"]):
            start, end = source["start_frame"], source["end_frame"]
            gap = round(row["gap_s"] * 16000) if index else 0
            if start != previous + gap or end - start != source["frames"]:
                raise ValueError("낭독 원음/무음 연결 좌표 불일치")
            if pcm[previous * 2 : start * 2] != b"\0\0" * gap:
                raise ValueError("추가 무음 PCM 불일치")
            if hashlib.sha256(pcm[start * 2 : end * 2]).hexdigest() != source["pcm16_sha256"]:
                raise ValueError("낭독 원음 PCM 불일치")
            previous = end
        if previous * 2 != len(pcm) or row["reference"] != " ".join(
            s["reference"] for s in row["source_timeline"]
        ):
            raise ValueError("낭독 최종 길이/연결 전사 불일치")


def prepare(root, output):
    relative = output.relative_to(root)
    if len(relative.parts) < 3 or relative.parts[:2] != ("data", "evaluation"):
        raise ValueError("data/evaluation/ 아래 새 경로가 필요합니다.")
    if output.exists():
        return verify(root, output)
    if shutil.which("ffmpeg") is None:
        raise ValueError("기존 Host ffmpeg가 필요합니다.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 실패한 준비도 별도 .preparing 폴더에 보존한다.
    stage = Path(tempfile.mkdtemp(prefix=output.name + ".preparing-", dir=output.parent))
    (stage / "wav").mkdir()
    (stage / "original").mkdir()
    natural = prepare_natural(root, stage, relative)
    reading = prepare_read(root, stage, relative)
    rows = natural + reading
    for name, subset in (("manifest", rows), ("natural", natural), ("reading", reading)):
        write_jsonl(stage / (name + ".jsonl"), subset)
    write_json(
        stage / "statistics.json",
        {
            "count": len(rows),
            "duration_s": sum(r["duration_s"] for r in rows),
            "min_duration_s": min(r["duration_s"] for r in rows),
            "max_duration_s": max(r["duration_s"] for r in rows),
            "condition_counts": dict(Counter(r["condition"] for r in rows)),
            "gender_counts": dict(Counter(r["gender"] for r in rows)),
            "natural_speakers": len({r["speaker_id"] for r in natural}),
            "normalized_reference_chars": sum(r["reference_normalized_chars"] for r in rows),
        },
    )
    info = finish(
        stage,
        output,
        {
            "recipe": RECIPE,
            "source_archives": ARCHIVES,
            "zeroth_prefix_sha256": zeroth.PREFIX_SOURCE[1],
            "excluded_fixed100_manifest_sha256": digest(
                root / "data/evaluation/edge100-20261109-01/manifest.jsonl"
            ),
            "preparation_script_sha256": digest(Path(__file__)),
            "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
            "attribution": [
                "Yun et al. (2015), The Korean Corpus of Spontaneous Speech, 7(2), 103–109. https://www.openslr.org/113/ CC BY-NC 2.0",
                "Zeroth-Korean, https://www.openslr.org/40/ CC BY 4.0",
            ],
            "limitations": "배포 전사 기반 개발용. 독립 청취/단어시각 검수·모델 학습 중복 확인 없음. 자연발화/낭독 연결을 별도 집계. CER·지연·RP5 추론 미측정.",
        },
    )
    verify(root, output)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = (root / args.output).resolve()
    info = (verify if args.verify_only else prepare)(root, output)
    print(json.dumps({"directory": str(output), "files": len(info["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
