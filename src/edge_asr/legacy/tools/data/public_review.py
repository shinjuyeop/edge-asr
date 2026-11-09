"""공개 자료의 무결성·시간 라벨 검사와 미검수 후보 생성. 추론/정답 확정은 하지 않는다."""

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from edge_asr.artifacts import digest
from edge_asr.dataset import load_audio
from edge_asr.textgrid import read_textgrid

RECIPE = {
    "name": "public-vad-review-v1",
    "short_s": [0.08, 0.5],
    "long_s": [8, 20],
    "per_speaker": {"short": 2, "long": 1},
    "context_s": 0.5,
    "order": "TextGrid 파일명, utt.ortho. 구간 순서",
    "selection_scope": "개발용 검수 후보. 최종 평가 분리 전이며 대표성을 주장하지 않음",
}


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("잘못된 자료 내부 경로")
    return path


def verify_source(directory):
    record = json.loads((directory / "acquisition.json").read_text(encoding="utf-8"))
    for entry in record["files"]:
        path = directory.joinpath(*safe_member(entry["path"]).parts).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path) != entry["sha256"]:
            raise ValueError(f"확보 기록과 원본 해시 불일치: {entry['path']}")
    return record


def seoul_candidates(archive):
    audit, candidates, used = [], [], Counter()
    with zipfile.ZipFile(archive) as handle:
        infos = sorted(
            (
                i
                for i in handle.infolist()
                if i.filename.endswith(".TextGrid") and not i.filename.startswith("__MACOSX/")
            ),
            key=lambda i: i.filename,
        )
        if not infos or len({PurePosixPath(i.filename).name for i in infos}) != len(infos):
            raise ValueError("TextGrid 목록 없음 또는 중복 멤버")
        for info in infos:
            name = safe_member(info.filename).name
            if info.file_size > 8 * 1024**2 or not re.fullmatch(
                r"s\d{2}[mf]\d{2}[mf]\d\.TextGrid", name
            ):
                raise ValueError("지원하지 않는 Seoul Corpus 파일")
            blob = handle.read(info)  # ZIP CRC도 검사. 아카이브 파일을 추출하지 않는다.
            row = {"member": info.filename, "sha256": hashlib.sha256(blob).hexdigest()}
            try:
                grid = read_textgrid(blob)
                utterances = [t for t in grid.tiers if t.name == "utt.ortho."]
                phones = [t for t in grid.tiers if t.name == "phoneme"]
                if len(utterances) != 1 or len(phones) != 1:
                    raise ValueError("단일 utt.ortho./phoneme tier가 필요합니다.")
            except (ValueError, UnicodeError) as error:
                audit.append({**row, "status": "quarantined", "error": str(error)})
                continue
            tags = Counter(p.text for p in phones[0].intervals if "<" in p.text or ">" in p.text)
            row.update(
                status="parsed",
                duration_s=grid.end - grid.start,
                tier_names=[t.name for t in grid.tiers],
                special_labels=dict(tags),
                utterance_intervals=len(utterances[0].intervals),
            )
            audit.append(row)
            speaker = name[:3]
            for index, utterance in enumerate(utterances[0].intervals, 1):
                duration = utterance.end - utterance.start
                if not utterance.text.strip() or "<" in utterance.text or ">" in utterance.text:
                    continue
                kind = next(
                    (
                        k
                        for k in ("short", "long")
                        if RECIPE[k + "_s"][0] <= duration <= RECIPE[k + "_s"][1]
                    ),
                    None,
                )
                if kind is None or used[(speaker, kind)] >= RECIPE["per_speaker"][kind]:
                    continue
                used[(speaker, kind)] += 1
                candidates.append(
                    {
                        "id": f"seoul-{Path(name).stem}-u{index:04d}",
                        "source": "seoul_corpus",
                        "speaker_id": speaker,
                        "category": kind,
                        "label_member": info.filename,
                        "label_sha256": row["sha256"],
                        "source_audio_basename": Path(name).stem + ".flac",
                        "source_utterance_s": [utterance.start, utterance.end],
                        "requested_clip_s": [
                            max(grid.start, utterance.start - 0.5),
                            min(grid.end, utterance.end + 0.5),
                        ],
                        "context_special_labels": sorted(
                            {
                                p.text
                                for p in phones[0].intervals
                                if p.start < utterance.end + 0.5
                                and p.end > utterance.start - 0.5
                                and ("<" in p.text or ">" in p.text)
                            }
                        ),
                        "suggested_transcript": utterance.text,
                        "annotation_status": "unreviewed",
                        "reference": None,
                        "reference_speech_intervals": None,
                        "audio_path": None,
                        "expected_speech": None,
                        "split": "development_candidate",
                    }
                )
    return audit, candidates


def prepare(root, seoul, noises, output):
    root, seoul, noises, output = (p.resolve() for p in (root, seoul, noises, output))
    if (
        any(not p.is_relative_to(root / "data") for p in (seoul, noises, output))
        or output == root / "data"
    ):
        raise ValueError("프로젝트 data/ 아래의 별도 경로가 필요합니다.")
    if output.exists():
        raise FileExistsError("기존 검수 자료를 덮어쓰지 않습니다.")
    source = verify_source(seoul)
    noise_source = verify_source(noises)
    audit, candidates = seoul_candidates(seoul / "label-TextGrid.zip")
    for entry in noise_source["files"]:
        if not entry["path"].endswith(".wav"):
            continue
        path = noises / entry["path"]
        audio = load_audio(path, 16000)
        candidates.append(
            {
                "id": path.stem,
                "source": "rirs_pointsource",
                "category": "noise_candidate",
                "audio_path": path.relative_to(root).as_posix(),
                "audio_sha256": audio.sha256,
                "duration_s": audio.duration_s,
                "annotation_status": "unreviewed",
                "reference": None,
                "reference_speech_intervals": None,
                "expected_speech": None,
                "split": "development_candidate",
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in (("label-audit.jsonl", audit), ("review-candidates.jsonl", candidates)):
        with (output / name).open("x", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "schema": "public-vad-review-v1",
        "status": "human_review_required",
        "recipe": RECIPE,
        "source_licenses": {
            "seoul": source["license"],
            "rirs": noise_source["resource_license_label"],
        },
        "source_receipts": {
            str(p.relative_to(root)): digest(p)
            for p in (seoul / "acquisition.json", noises / "acquisition.json")
        },
        "label_files": len(audit),
        "parsed": sum(r["status"] == "parsed" for r in audit),
        "quarantined": [r["member"] for r in audit if r["status"] != "parsed"],
        "candidate_counts": dict(Counter(r["category"] for r in candidates)),
        "candidate_speakers": len({r["speaker_id"] for r in candidates if "speaker_id" in r}),
        "files": {p.name: digest(p) for p in sorted(output.iterdir())},
        "limits_ko": [
            "모델 출력/VAD 검출로 후보를 선택하거나 정답을 만들지 않았다.",
            "reference=null이므로 기존 ASR 평가기는 이 목록을 거부한다. 빈 전사로 바꾸지 않는다.",
            "모든 특수 태그와 미해석 파일은 감사 목록에 보존한다. <IVER> 등을 무음으로 간주하지 않는다.",
            "음성 경계·화자·태그 의미·잡음 안 말소리를 청취/파형으로 검수해야 한다.",
            "화자별 상한은 균등한 검수량을 위한 규칙이다. 훈련 중복과 최종 독립성은 미확인.",
        ],
    }
    with (output / "provenance.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--seoul", type=Path, default=Path("data/sources/seoul-corpus-20261029-01"))
    parser.add_argument("--noises", type=Path, default=Path("data/sources/rirs-noises-20261029-01"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.root, args.root / args.seoul, args.root / args.noises, args.root / args.output
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
