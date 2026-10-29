"""고정한 공개 검수 후보를 원본 보존 WAV/로컬 청취 페이지로 만든다. 정답 확정·추론은 하지 않는다."""

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
import wave
import zipfile
from pathlib import Path

from edge_asr.artifacts import digest, write_json
from edge_asr.dataset import load_audio
from edge_asr.tools.data.public_review import safe_member

SOURCE_NOTICES = {
    "seoul": {
        "url": "https://www.openslr.org/113/",
        "license": "CC BY-NC 2.0",
        "credit": "Yun, W., Yoon, K., Park, S., Lee, J., Cho, S., Kang, D., Byun, K., Hahn, H., Kim, J. (2015). The Korean Corpus of Spontaneous Speech. Journal of the Korean Society of Speech Sciences, 7(2), 103–109.",
        "modifications": "원본 보존. mono/16kHz/PCM16 변환 및 후보 앞뒤 문맥 포함 구간 절취.",
    },
    "rirs_pointsource": {
        "url": "https://www.openslr.org/28/",
        "resource_license_label": "Apache 2.0",
        "credit": "RIRS_NOISES pointsource_noises; MUSAN Freesound subset",
        "subset_license_statement": "배포물 pointsource_noises/LICENSE는 선택된 녹음이 Freesound에서 Public Domain으로 표시됐다고 설명함.",
        "modifications": "원본 WAV를 바이트 그대로 복사. 비음성 정답으로 확정하지 않음.",
    },
}


def slice_wav(source, destination, start_s, end_s):
    """전체 음원을 먼저 16k로 변환한 뒤 같은 샘플 격자에서 잘라 재현한다."""
    with wave.open(str(source), "rb") as stream:
        if (
            stream.getframerate(),
            stream.getnchannels(),
            stream.getsampwidth(),
            stream.getcomptype(),
        ) != (16000, 1, 2, "NONE"):
            raise ValueError("변환된 mono PCM16/16kHz WAV가 필요합니다.")
        frames = stream.getnframes()
        start, end = round(start_s * 16000), round(end_s * 16000)
        if not 0 <= start < end <= frames:
            raise ValueError("라벨의 요청 구간이 실제 음원 범위를 벗어납니다.")
        stream.setpos(start)
        pcm = stream.readframes(end - start)
        if len(pcm) != (end - start) * 2:
            raise ValueError("잘린 변환 WAV")
    with destination.open("xb") as raw, wave.open(raw, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm)
    return {
        "source_resampled_frames": frames,
        "source_clip_frames_16k": [start, end],
        "actual_clip_s": [start / 16000, end / 16000],
    }


def materialize(root, review, source_zip, output, *, use_purpose):
    if use_purpose != "noncommercial-research":
        raise ValueError("Seoul Corpus의 비상업 연구 이용 목적 확인이 필요합니다.")
    root, review, source_zip, output = (p.resolve() for p in (root, review, source_zip, output))
    if (
        any(not p.is_relative_to(root / "data") for p in (review, source_zip, output))
        or output == root / "data"
    ):
        raise ValueError("프로젝트 data/ 아래의 별도 경로가 필요합니다.")
    if output.exists():
        raise FileExistsError("기존 검수 음원을 덮어쓰지 않습니다.")
    provenance = json.loads((review / "provenance.json").read_text(encoding="utf-8"))
    manifest = review / "review-candidates.jsonl"
    if digest(manifest) != provenance["files"][manifest.name]:
        raise ValueError("선정 후 후보 목록이 변경됐습니다.")
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    ids = [row["id"] for row in rows]
    if (
        not ids
        or len(set(ids)) != len(ids)
        or any(not re.fullmatch(r"[a-zA-Z0-9_-]+", i) for i in ids)
    ):
        raise ValueError("잘못되거나 중복된 후보 ID")
    if any(
        row["reference"] is not None or row["annotation_status"] != "unreviewed" for row in rows
    ):
        raise ValueError("원래의 미검수 후보 목록이 필요합니다.")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Host의 ffmpeg가 필요합니다. 자동 설치하지 않습니다.")
    info = {
        "status": "preparing",
        "source_candidates_sha256": digest(manifest),
        "source_zip_sha256": digest(source_zip),
        "selection_recipe": provenance["recipe"],
        "ffmpeg": subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0],
        "conversion": "각 원본 FLAC 전체를 mono PCM16/16000Hz로 변환 후 round(seconds*16000)의 [start,end) 격자로 자름",
        "seoul_use_purpose": use_purpose,
        "source_notices": SOURCE_NOTICES,
        "asr_or_vad_run": False,
        "ground_truth_finalized": False,
        "source_audio": {},
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "audio").mkdir()
    (output / "original").mkdir()
    write_json(output / "provenance.json", info)
    try:
        with (
            zipfile.ZipFile(source_zip) as archive,
            tempfile.TemporaryDirectory(dir=output, prefix=".decode-") as tmp,
        ):
            members = {}
            for entry in archive.infolist():
                if entry.filename.startswith("__MACOSX/") or not entry.filename.endswith(".flac"):
                    continue
                name = safe_member(entry.filename).name
                if name in members:
                    raise ValueError("원본 음원 basename 중복")
                members[name] = entry
            groups = {}
            for row in rows:
                if row["source"] == "seoul_corpus":
                    groups.setdefault(row["source_audio_basename"], []).append(row)
                elif row["source"] == "rirs_pointsource":
                    path = (root / row["audio_path"]).resolve()
                    if (
                        not path.is_relative_to(root / "data")
                        or digest(path) != row["audio_sha256"]
                    ):
                        raise ValueError("잡음 원본 경로·해시 불일치")
                    with (
                        path.open("rb") as src,
                        (output / "audio" / (row["id"] + ".wav")).open("xb") as dst,
                    ):
                        shutil.copyfileobj(src, dst)
                else:
                    raise ValueError("알 수 없는 후보 출처")
            for name, group in sorted(groups.items()):
                entry = members[name]
                if not 0 < entry.file_size < 100 * 1024**2:
                    raise ValueError("예상 범위 밖의 원본 FLAC 크기")
                original = output / "original" / name
                with archive.open(entry) as src, original.open("xb") as dst:
                    shutil.copyfileobj(src, dst)  # 끝까지 읽어 ZIP CRC 검증
                info["source_audio"][name] = {"member": entry.filename, "sha256": digest(original)}
                converted = Path(tmp) / "decoded.wav"
                subprocess.run(
                    [
                        ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
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
                    timeout=120,
                )
                for row in group:
                    row.update(
                        slice_wav(
                            converted,
                            output / "audio" / (row["id"] + ".wav"),
                            *row["requested_clip_s"],
                        )
                    )
                    row["source_audio_sha256"] = info["source_audio"][name]["sha256"]
                print(f"검수 음원 준비: {name} ({len(group)}구간)", flush=True)
        cards = []
        for row in rows:
            path = output / "audio" / (row["id"] + ".wav")
            audio = load_audio(path, 16000)
            row.update(
                audio_path=path.relative_to(root).as_posix(),
                audio_sha256=audio.sha256,
                duration_s=audio.duration_s,
            )
            esc = html.escape
            details = row.get("suggested_transcript", "전사·음성 포함 여부 미확인")
            tags = ", ".join(row.get("context_special_labels", []))
            cards.append(
                f"<article><h2>{esc(row['id'])} · {esc(row['category'])}</h2>"
                f'<audio controls preload="none" src="audio/{row["id"]}.wav"></audio>'
                f"<p>배포 라벨 후보: {esc(details)}</p><p>주변 태그: {esc(tags)}</p>"
                "<p>미검수 · 정답 미확정</p></article>"
            )
        (output / "review.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        (output / "index.html").write_text(
            '<!doctype html><html lang="ko"><meta charset="utf-8">'
            "<title>공개 VAD 자료 검수</title><style>body{max-width:850px;margin:32px auto;font-family:sans-serif;padding:16px}"
            "article{border-top:1px solid #ccc;padding:16px 0}h2{font-size:18px}audio{width:100%}</style>"
            "<h1>공개 VAD 자료 검수 후보</h1><p>개인·학술 비상업 연구용. 음성 구간·말소리 포함 여부와 전사를 확인하기 전에는 평가 정답으로 사용하지 않습니다. "
            "앞뒤 문맥 0.5초에는 다른 발화가 있을 수 있습니다. 재생만으로 검수 완료 처리되지 않습니다.</p>"
            + "\n".join(cards)
            + "</html>",
            encoding="utf-8",
        )
        info.update(
            status="human_review_required",
            clips=len(rows),
            total_clip_duration_s=sum(r["duration_s"] for r in rows),
            files={
                str(p.relative_to(output)): digest(p)
                for p in sorted(output.rglob("*"))
                if p.is_file() and p.name != "provenance.json"
            },
        )
    except Exception as error:
        info.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        write_json(output / "provenance.json", info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--use-purpose", choices=["noncommercial-research"], required=True)
    args = parser.parse_args()
    result = materialize(
        args.root,
        args.root / args.review,
        args.root / args.source_zip,
        args.root / args.output,
        use_purpose=args.use_purpose,
    )
    print(
        json.dumps(
            {k: result[k] for k in ("status", "clips", "total_clip_duration_s")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
