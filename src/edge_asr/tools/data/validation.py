"""새 문장 평가 30개와 기존 PCM의 30분 지속 입력을 별도 경로에 준비한다."""

import argparse
import json
import shutil
import subprocess
import urllib.parse
import wave
from collections import Counter
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json, write_jsonl
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.metrics import normalize
from edge_asr.tools.data.external import DATASET, REVISION, validate_rows, verify_existing
from edge_asr.tools.data.kss import fetch

COUNT = 30


def select_rows(rows, seen, count=COUNT):
    ids = {r["sentence_id"] for r in seen if "sentence_id" in r}
    texts = {normalize(r["reference"]) for r in seen}
    selected, decisions = [], []
    for item in rows:
        row = item["row"]
        text = normalize(row["transcription"])
        reason = (
            "sentence_id_seen"
            if row["id"] in ids
            else "normalized_reference_seen"
            if text in texts
            else "empty_normalized_reference"
            if not text
            else "selected"
        )
        decisions.append({"row": item["row_idx"], "decision": reason})
        if reason != "selected":
            continue
        selected.append(item)
        ids.add(row["id"])
        texts.add(text)
        if len(selected) == count:
            return selected, decisions
    raise ValueError("고정한 처음100개 행 안에서 겹치지 않는30개 문장을 확보하지 못했습니다.")


def seal(output, recipe, details):
    write_json(output / "preparation.json", {"status": "completed", "recipe": recipe})
    result = {
        "recipe": recipe,
        **details,
        "files": {
            str(p.relative_to(output)): digest(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p.name != "provenance.json"
        },
    }
    write_json(output / "provenance.json", result)
    return result


def prepare_fleurs(root, output):
    relative = output.relative_to(root)
    if relative.parts[0] != "data":
        raise ValueError("data/ 아래 새 경로가 필요합니다.")
    previous = [
        root / "data/evaluation/fleurs-ko-test100/manifest.jsonl",
        root / "data/evaluation/kss-dev50/manifest.jsonl",
    ]
    recipe = {
        "name": "prospective-fleurs30-v1",
        "dataset": DATASET,
        "revision": REVISION,
        "config": "ko_kr",
        "source_split": "validation",
        "scan_offset": 0,
        "scan_count": 100,
        "selected_count": COUNT,
        "normalization": "ko_cer_v1",
        "selection": "배포 행 순서. 기존 문장ID/정규화 정답 및 이번 선택 중복을 제외한 처음30개",
        "excluded_manifests": {str(p.relative_to(root)): digest(p) for p in previous},
    }
    if output.exists():
        return verify_existing(output, recipe)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Host ffmpeg가 필요합니다.")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "preparation.json", {"status": "preparing", "recipe": recipe})
    try:
        url = "https://datasets-server.huggingface.co/rows?" + urllib.parse.urlencode(
            {
                "dataset": DATASET,
                "config": "ko_kr",
                "split": "validation",
                "offset": 0,
                "length": 100,
            }
        )
        blob, headers = fetch(url, 2 * 1024**2)
        (output / "source_rows.json").write_bytes(blob)
        rows = validate_rows(
            json.loads(blob), headers.get("x-revision"), source_split="validation", count=100
        )
        seen = [r for p in previous for r in read_jsonl(p)]
        selected, decisions = select_rows(rows, seen, COUNT)
        write_json(
            output / "selection.json",
            {"decisions": decisions, "selected_rows": [r["row_idx"] for r in selected]},
        )
        card_url = f"https://huggingface.co/datasets/{DATASET}/raw/{REVISION}/README.md"
        card, _ = fetch(card_url, 2 * 1024**2)
        (output / "SOURCE_README.md").write_bytes(card)
        (output / "original").mkdir()
        (output / "wav").mkdir()
        manifests, details = [], []
        old_audio = {r["audio_sha256"] for r in seen}
        for item in selected:
            index, row = item["row_idx"], item["row"]
            name = f"{index:04d}.wav"
            original, converted = output / "original" / name, output / "wav" / name
            blob, _ = fetch(row["audio"][0]["src"], 8 * 1024**2)
            original.write_bytes(blob)
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
            if len(audio.samples) != row["num_samples"] or audio.sha256 in old_audio:
                raise ValueError(
                    "오디오 프레임 불일치 또는 기존/선택 음원 중복. 다른 행으로 교체하지 않습니다."
                )
            old_audio.add(audio.sha256)
            manifests.append(
                {
                    "id": f"fleurs-ko-validation-{index:04d}",
                    "audio_path": str(relative / "wav" / name),
                    "audio_sha256": audio.sha256,
                    "reference": row["transcription"],
                    "split": "prospective_evaluation",
                    "condition": "fleurs_read_speech",
                    "sentence_id": row["id"],
                    "source_row": index,
                    "gender": row["gender"],
                    "speaker_id": None,
                    "dataset_revision": REVISION,
                    "license": "cc-by-4.0",
                }
            )
            details.append(
                {"id": manifests[-1]["id"], **audio.metadata(), "original_sha256": digest(original)}
            )
            print(f"새 문장 {len(manifests)}/{COUNT}: {audio.duration_s:.3f}초", flush=True)
        write_jsonl(output / "manifest.jsonl", manifests)
        return seal(
            output,
            recipe,
            {
                "audio": details,
                "rows_url": url,
                "dataset_card": card_url,
                "ffmpeg": subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0],
                "attribution": "Conneau et al. (2022), FLEURS, Google, CC BY 4.0",
                "speaker_count": None,
                "speaker_independence_verified": False,
                "model_training_overlap_verified": False,
                "scope": "설정 고정 뒤 처음 평가하는 프로젝트 새 문장. 낭독이며 화자 독립/자연 대화/잡음 평가 아님",
                "gender_counts": dict(Counter(str(r["gender"]) for r in manifests)),
            },
        )
    except BaseException as error:
        write_json(
            output / "preparation.json",
            {"status": "failed", "recipe": recipe, "error": repr(error)},
        )
        raise


def prepare_sustained(root, output, repeats=15):
    relative = output.relative_to(root)
    if relative.parts[0] != "data" or type(repeats) is not int or not 1 <= repeats <= 15:
        raise ValueError("data/ 아래 새 경로와 1~15회 반복이 필요합니다.")
    manifest = root / "data/evaluation/m23-stress/long-120.jsonl"
    samples, sha = load_manifest(manifest, root)
    if len(samples) != 1:
        raise ValueError("기존 합성 원본 하나가 필요합니다.")
    sample = samples[0]
    if digest(sample.audio_path) != sample.audio_sha256:
        raise ValueError("기존 원본 해시 불일치")
    recipe = {
        "name": "sustained-original-pcm-v1",
        "manifest_sha256": sha,
        "source_audio_sha256": sample.audio_sha256,
        "repeats": repeats,
        "policy": "전체 PCM 단순 연결, gain/gap/잘림 없음. 단일 원본으로 Zip/VAD 중간 reset 없음",
    }
    if output.exists():
        return verify_existing(output, recipe)
    output.mkdir(parents=True, exist_ok=False)
    with wave.open(str(sample.audio_path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (
            1,
            2,
            16000,
            "NONE",
        ):
            raise ValueError("16kHz mono PCM16 원본이 필요합니다.")
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
        if len(pcm) != frames * 2:
            raise ValueError("원본 프레임 누락")
    target = output / "continuous.wav"
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        for _ in range(repeats):
            wav.writeframesraw(pcm)
    write_jsonl(
        output / "manifest.jsonl",
        [
            {
                "id": "kss-synthetic-continuous",
                "audio_path": str(relative / "continuous.wav"),
                "audio_sha256": digest(target),
                "reference": " ".join([sample.reference] * repeats),
                "split": "engineering_stress",
                "condition": "synthetic_repeated_audio_not_independent_accuracy",
            }
        ],
    )
    return seal(
        output,
        recipe,
        {
            "frames": frames * repeats,
            "audio_s": frames * repeats / 16000,
            "cycle_frames": frames,
            "attribution": "Kyubyong Park, KSS Dataset (2018), CC BY-NC-SA 4.0",
            "source": str(sample.audio_path.relative_to(root)),
            "cycle_reference": sample.reference,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("fleurs", "sustained"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    result = (prepare_fleurs if args.kind == "fleurs" else prepare_sustained)(
        root, args.output.resolve()
    )
    print(json.dumps({"output": str(args.output), "recipe": result["recipe"]}, ensure_ascii=False))
