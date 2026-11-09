"""개발 WAV를 1/2/3개 겹친 지속 VAD 공학 입력. 전사/정확도 정답은 만들지 않는다."""

import argparse
import hashlib
import json
from array import array
from pathlib import Path

from edge_asr.artifacts import digest, read_jsonl, write_json
from edge_asr.audio_policy import preprocess
from edge_asr.dataset import load_audio, load_manifest, pcm_bytes, write_wav


def mix_pcm(pcm, shifts, frames):
    """고정 순환 위상의 산술 평균. 입력·출력은 PCM16이며 clipping 없이 혼합한다."""
    if (
        not pcm
        or type(frames) is not int
        or frames < 1
        or not 1 <= len(shifts) <= 3
        or any(type(s) is not int or not 0 <= s < len(pcm) for s in shifts)
        or any(type(x) is not int or not -32768 <= x <= 32767 for x in pcm)
    ):
        raise ValueError("유효한 PCM16·1~3개 위상·양의 프레임 수가 필요합니다.")
    return array(
        "h",
        (
            round(sum(pcm[(i + shift) % len(pcm)] for shift in shifts) / len(shifts))
            for i in range(frames)
        ),
    )


def prepare(root, manifest, output, *, duration_s=90):
    root, output = root.resolve(), output.resolve()
    if (
        not output.is_relative_to(root / "data")
        or type(duration_s) is not int
        or not 1 <= duration_s <= 90
    ):
        raise ValueError("새 data/ 경로와 1~90초 정수 길이가 필요합니다.")
    if output.exists():
        raise FileExistsError(output)
    samples, manifest_sha = load_manifest(manifest, root)
    metadata = {r["id"]: r for r in read_jsonl(manifest)}
    base, sources = array("h"), []
    for sample in samples:
        if sample.split != "development" or not sample.audio_path.is_relative_to(root / "data"):
            raise ValueError("data/ 아래 개발 자료만 사용합니다. 보류/평가 집합은 거부합니다.")
        audio = load_audio(sample.audio_path, 16000)
        if audio.sha256 != sample.audio_sha256:
            raise ValueError("개발 원본 SHA-256 불일치")
        pcm, _ = preprocess(audio.samples, "identity")
        begin = len(base)
        base.extend(pcm)
        sources.append(
            {
                "id": sample.id,
                "audio_sha256": audio.sha256,
                "audio_path": str(sample.audio_path.relative_to(root)),
                "base_frames": [begin, len(base)],
                **{
                    k: metadata[sample.id].get(k)
                    for k in ("dataset", "dataset_revision", "license")
                },
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    cases, files = [], {}
    for count in (1, 2, 3):
        shifts = [len(base) * n // count for n in range(count)]
        pcm = mix_pcm(base, shifts, duration_s * 16000)
        folder = output / f"tracks-{count}"
        folder.mkdir()
        wav = folder / "audio.wav"
        write_wav(wav, pcm)
        spec = {
            "schema": "vad-duration-input-v1",
            "audio_path": str(wav.relative_to(root)),
            "audio_sha256": digest(wav),
            "frames": len(pcm),
            "sample_rate": 16000,
            "source_manifest_sha256": manifest_sha,
            "source_manifest": str(manifest.resolve().relative_to(root)),
            "sources": sources,
            "base_frames": len(base),
            "track_count": count,
            "phase_frames": shifts,
            "mixing": "PCM16 arithmetic mean, round ties to even, no gain/limiter/filter",
            "expected_processed": {},
            "reference": None,
            "scope_ko": "기존 개발 발화를 고정 위상으로 겹친 비정상 공학 입력. 1트랙은 대조군. 모델 출력으로 고르지 않으며 자연 장문/CER/최종 독립 평가에 사용하지 않음.",
        }
        audio = load_audio(wav, 16000)
        for policy in ("identity", "rms26"):
            processed, info = preprocess(audio.samples, policy)
            spec["expected_processed"][policy] = {
                "pcm_sha256": hashlib.sha256(pcm_bytes(processed)).hexdigest(),
                "newly_clipped_samples": info["newly_clipped_samples"],
            }
        write_json(folder / "manifest.json", spec)
        for path in (wav, folder / "manifest.json"):
            files[str(path.relative_to(output))] = digest(path)
        cases.append(str((folder / "manifest.json").relative_to(root)))
    receipt = {
        "schema": "vad-overlap-preparation-v1",
        "source_manifest_sha256": manifest_sha,
        "duration_s": duration_s,
        "source_wavs": len(samples),
        "source_frames": len(base),
        "cases": cases,
        "files": files,
        "selection_ko": "실행 전 1/2/3트랙과 위상 공식 고정. 같은 개발 목록 전체의 순서 유지. 임계값/가중치/실서비스 전처리는 변경하지 않음.",
    }
    write_json(output / "provenance.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/evaluation/kss-dev50/manifest.jsonl")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, choices=(45, 60, 90), default=90)
    args = parser.parse_args()
    info = prepare(args.root, args.manifest, args.output, duration_s=args.duration)
    print(json.dumps({"output": str(args.output), "cases": info["cases"]}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
