"""Host에서 FLEURS ko_kr test 100개와 KSS 기반 길이/무음 스트레스 자료 준비."""

import argparse
import json
import shutil
import subprocess
import tempfile
import urllib.parse
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from edge_asr.artifacts import write_json
from edge_asr.artifacts import write_jsonl as write_manifest
from edge_asr.dataset import load_audio, load_manifest, write_wav
from edge_asr.tools.data.kss import fetch, sha256

DATASET = "google/fleurs"
REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
COUNT = 100
RECIPE = {
    "name": "fleurs-ko-test100-v1",
    "dataset": DATASET,
    "revision": REVISION,
    "config": "ko_kr",
    "source_split": "test",
    "offset": 0,
    "count": COUNT,
    "reference_field": "transcription",
    "license": "cc-by-4.0",
}


def validate_rows(value, revision):
    if (
        revision != REVISION
        or value.get("partial") is not False
        or len(value.get("rows", [])) != COUNT
    ):
        raise ValueError("FLEURS 고정 리비전의 전체 100개 행이 필요합니다.")
    for index, item in enumerate(value["rows"]):
        row = item["row"]
        if (
            item["row_idx"] != index
            or item.get("truncated_cells")
            or row.get("language") != "Korean"
        ):
            raise ValueError("잘못된 행 번호·언어·잘린 전사")
        if not isinstance(row.get("transcription"), str) or not row["transcription"].strip():
            raise ValueError("배포처 정답이 필요합니다.")
        if type(row.get("num_samples")) is not int or not 0 < row["num_samples"] <= 16000 * 60:
            raise ValueError("잘못된 오디오 길이")
        audio = row.get("audio", [])
        if len(audio) != 1:
            raise ValueError("오디오 URL 하나가 필요합니다.")
        url = urllib.parse.urlsplit(audio[0]["src"])
        expected = f"/cached-assets/{DATASET}/--/{REVISION}/--/ko_kr/test/{index}/audio/audio.wav"
        if (
            url.scheme != "https"
            or url.netloc != "datasets-server.huggingface.co"
            or url.path != expected
        ):
            raise ValueError("고정 출처 이외의 오디오 URL")
    return value["rows"]


def verify_existing(destination, recipe):
    provenance = json.loads((destination / "provenance.json").read_text())
    if provenance["recipe"] != recipe:
        raise ValueError("기존 자료의 준비 조건이 다릅니다. 덮어쓰지 않습니다.")
    actual = {
        str(p.relative_to(destination))
        for p in destination.rglob("*")
        if p.is_file() and p.name != "provenance.json"
    }
    if actual != set(provenance["files"]):
        raise ValueError("기존 파일 목록 변경")
    for name, checksum in provenance["files"].items():
        path = destination / name
        if path.is_symlink() or sha256(path) != checksum:
            raise ValueError(f"기존 파일 변경: {name}")
    return provenance


def finish(stage, destination, provenance):
    provenance["prepared_at_utc"] = datetime.now(timezone.utc).isoformat()
    provenance["files"] = {
        str(p.relative_to(stage)): sha256(p) for p in sorted(stage.rglob("*")) if p.is_file()
    }
    write_json(stage / "provenance.json", provenance)
    if destination.exists():
        raise ValueError("기존 자료를 덮어쓰지 않습니다.")
    stage.rename(destination)
    return provenance


def prepare_fleurs(root, destination):
    relative = destination.relative_to(root)
    if relative.parts[0] != "data":
        raise ValueError("data/ 안의 경로가 필요합니다.")
    if destination.exists():
        return verify_existing(destination, RECIPE)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Host의 ffmpeg가 필요합니다.")
    url = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode(
        {"dataset": DATASET, "config": "ko_kr", "split": "test", "offset": 0, "length": COUNT}
    )
    blob, headers = fetch(url, 2 * 1024**2)
    rows = validate_rows(json.loads(blob), headers.get("x-revision"))
    card_url = f"https://huggingface.co/datasets/{DATASET}/raw/{REVISION}/README.md"
    card, _ = fetch(card_url, 2 * 1024**2)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".fleurs-", dir=destination.parent))
    try:
        (stage / "original").mkdir()
        (stage / "wav").mkdir()
        (stage / "source_rows.json").write_bytes(blob)
        (stage / "SOURCE_README.md").write_bytes(card)
        manifest, details = [], []
        for index, item in enumerate(rows):
            row = item["row"]
            name = f"{index:04d}.wav"
            original, converted = stage / "original" / name, stage / "wav" / name
            audio_blob, _ = fetch(row["audio"][0]["src"], 8 * 1024**2)
            original.write_bytes(audio_blob)
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
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
                    "-fflags",
                    "+bitexact",
                    "-flags:a",
                    "+bitexact",
                    str(converted),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            audio = load_audio(converted, 16000)
            if len(audio.samples) != row["num_samples"]:
                raise ValueError("배포처 프레임 수와 변환된 오디오가 다릅니다.")
            manifest.append(
                {
                    "id": f"fleurs-ko-test-{index:04d}",
                    "audio_path": (relative / "wav" / name).as_posix(),
                    "audio_sha256": audio.sha256,
                    "reference": row["transcription"],
                    "split": "external_evaluation",
                    "condition": "fleurs_read_speech",
                    "source_row": index,
                    "sentence_id": row["id"],
                    "gender": row["gender"],
                    "speaker_id": None,
                    "dataset": DATASET,
                    "dataset_revision": REVISION,
                    "license": "cc-by-4.0",
                }
            )
            details.append(
                {
                    "row": index,
                    "sentence_id": row["id"],
                    "gender": row["gender"],
                    **audio.metadata(),
                    "original_sha256": sha256(original),
                }
            )
            print(f"FLEURS {index + 1}/{COUNT}: {audio.duration_s:.3f}초", flush=True)
        write_manifest(stage / "manifest.jsonl", manifest)
        return finish(
            stage,
            destination,
            {
                "recipe": RECIPE,
                "dataset_card": card_url,
                "rows_revision_header": headers.get("x-revision"),
                "rows_url": url,
                "attribution": "Conneau et al. (2022), FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech. Google, https://arxiv.org/abs/2205.12446",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "conversion": "배포 WAV → ffmpeg mono PCM16 16kHz; 프레임 수 유지, metadata 제거. 음량/무음/정답 보정 없음.",
                "ffmpeg": subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0],
                "sampling": "test 행 0~99 연속 선택, 모델 출력을 보기 전 고정. 무작위 대표 표본 아님.",
                "speaker_count": None,
                "speaker_count_reason": "제공 열에 화자 ID 없음. 성별 분포가 화자 수를 의미하지 않음.",
                "gender_counts": dict(Counter(str(row["gender"]) for row in manifest)),
                "unique_sentence_ids": len({row["sentence_id"] for row in manifest}),
                "training_overlap_verified": False,
                "audio": details,
            },
        )
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def prepare_stress(root, destination):
    relative = destination.relative_to(root)
    if relative.parts[0] != "data":
        raise ValueError("data/ 안의 경로가 필요합니다.")
    source = root / "data/evaluation/kss-dev50/manifest.jsonl"
    samples, checksum = load_manifest(source, root)
    recipe = {
        "name": "m23-stress-v1",
        "kss_manifest_sha256": checksum,
        "silence_s": [0.01, 0.1, 0.5, 1, 3, 10],
        "short": "KSS 개발 자료 중 길이순 10개, id로 동률 결정. 자르지 않음.",
        "synthetic_long_target_s": [30, 60, 120],
        "gap_s": 0.3,
    }
    if destination.exists():
        return verify_existing(destination, recipe)
    audio = {s.id: load_audio(s.audio_path, 16000) for s in samples}
    if any(s.audio_sha256 != audio[s.id].sha256 for s in samples):
        raise ValueError("기존 KSS 자산 해시 불일치")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".stress-", dir=destination.parent))
    try:
        (stage / "wav").mkdir()
        silence = []
        for index, duration in enumerate(recipe["silence_s"]):
            name = f"silence-{index}.wav"
            write_wav(stage / "wav" / name, b"\0\0" * round(duration * 16000))
            silence.append(
                {
                    "id": f"silence-{duration}s",
                    "audio_path": str(relative / "wav" / name),
                    "audio_sha256": sha256(stage / "wav" / name),
                    "reference": "",
                    "condition": "synthetic_zero_silence",
                    "split": "stress",
                }
            )
        write_manifest(stage / "silence.jsonl", silence)
        short = sorted(samples, key=lambda s: (audio[s.id].duration_s, s.id))[:10]
        write_manifest(
            stage / "short.jsonl",
            [
                {
                    "id": s.id,
                    "audio_path": str(s.audio_path.relative_to(root)),
                    "audio_sha256": audio[s.id].sha256,
                    "reference": s.reference,
                    "split": "stress",
                    "condition": "kss_short_complete_utterance",
                }
                for s in short
            ],
        )
        long_details = []
        for target in recipe["synthetic_long_target_s"]:
            chunks, sources, references, frames = [], [], [], 0
            for sample in samples:
                if chunks:
                    chunks.append(b"\0\0" * 4800)
                    frames += 4800
                with wave.open(str(sample.audio_path), "rb") as wav:
                    chunks.append(wav.readframes(wav.getnframes()))
                    frames += wav.getnframes()
                sources.append(sample.id)
                references.append(sample.reference)
                if frames >= target * 16000:
                    break
            if frames < target * 16000:
                raise ValueError("긴 입력을 만들 원본 발화가 부족합니다.")
            name = f"long-{target}.wav"
            write_wav(stage / "wav" / name, b"".join(chunks))
            write_manifest(
                stage / f"long-{target}.jsonl",
                [
                    {
                        "id": f"synthetic-long-{target}",
                        "audio_path": str(relative / "wav" / name),
                        "audio_sha256": sha256(stage / "wav" / name),
                        "reference": " ".join(references),
                        "split": "stress",
                        "condition": "synthetic_concatenated_kss",
                        "source_ids": sources,
                    }
                ],
            )
            long_details.append(
                {"target_s": target, "actual_s": frames / 16000, "source_ids": sources}
            )
        return finish(
            stage,
            destination,
            {
                "recipe": recipe,
                "synthetic_long": long_details,
                "short_durations_s": [audio[s.id].duration_s for s in short],
                "limitations": "낭독 연결 합성 입력. 자연 긴 대화 아님. 짧은 발화는 KSS와 중복, 외부 정확도 평가 아님. 무음 CER null, 비어 있지 않은 반환 별도 보고.",
            },
        )
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("fleurs", "stress"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    destination = (
        root / "data/evaluation" / ("fleurs-ko-test100" if args.kind == "fleurs" else "m23-stress")
    )
    value = (prepare_fleurs if args.kind == "fleurs" else prepare_stress)(root, destination)
    print(
        json.dumps(
            {"directory": str(destination), "files": len(value["files"])}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
