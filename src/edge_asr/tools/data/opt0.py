"""원본을 읽어 KSS 길이 표본과 과거 실패 PCM의 새 manifest를 만든다. 모델 실행 없음."""

import argparse
import hashlib
import json
import wave
from pathlib import Path

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.dataset import load_audio, load_manifest, write_wav


def prepare(root, kss_manifest, failed_predictions, output):
    samples, manifest_sha = load_manifest(kss_manifest, root)
    if len(samples) != 50:
        raise ValueError("고정한 KSS 개발 50개 manifest가 필요합니다.")
    audio = {s.id: load_audio(s.audio_path, 16000) for s in samples}
    if any(s.audio_sha256 != audio[s.id].sha256 for s in samples):
        raise ValueError("KSS 원본 SHA-256 불일치")
    ordered = sorted(samples, key=lambda s: (len(audio[s.id].samples), s.id))
    ranks = (0, 9, 19, 29, 39, 49)
    selected = [ordered[i] for i in ranks]
    failure = next(
        json.loads(line)
        for line in failed_predictions.read_text().splitlines()
        if json.loads(line)["id"] == "fleurs-ko-validation-0009"
    )
    source = root / "data/evaluation/fleurs-prospective30-20261106-01/wav/0009.wav"
    if digest(source) != failure["audio"]["sha256"]:
        raise ValueError("실패 원 발화의 SHA-256 불일치")
    with wave.open(str(source)) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("16000Hz mono PCM16 원본이 필요합니다.")
        pcm = wav.readframes(wav.getnframes())
    segments = failure["segmented"]["segments"]
    first, tail = segments
    for segment in segments:
        start, end = segment["context_start_frame"], segment["context_end_frame"]
        if hashlib.sha256(pcm[start * 2 : end * 2]).hexdigest() != segment["payload_sha256"]:
            raise ValueError("원시 구간의 PCM SHA-256 불일치")
    start, end = tail["context_start_frame"], tail["context_end_frame"]
    if end - start != 896:
        raise ValueError("고정 재현 대상은 896프레임이어야 합니다.")
    # 모든 원본 검증이 끝난 뒤 새 디렉터리만 만든다.
    output.mkdir(parents=True, exist_ok=False)
    baseline = [
        {
            "id": s.id,
            "audio_path": str(s.audio_path.relative_to(root)),
            "reference": s.reference,
            "audio_sha256": s.audio_sha256,
            "split": "development_pilot",
            "condition": "kss_length_rank_sample",
        }
        for s in selected
    ]
    write_jsonl(output / "baseline.jsonl", baseline)
    geometry = [
        ("tail-first", start, end),
        ("tail-repeat", start, end),
        ("preceding-5s", first["context_start_frame"], first["context_end_frame"]),
        ("tail-after-prefix", start, end),
        ("tail-context200ms", start - 3200, end),
        ("tail-context500ms", start - 8000, end),
        ("tail-context1000ms", start - 16000, end),
        ("full-utterance", 0, len(pcm) // 2),
    ]
    diagnostic = []
    for name, begin, stop in geometry:
        path = output / (name + ".wav")
        payload = pcm[begin * 2 : stop * 2]
        write_wav(path, payload)
        diagnostic.append(
            {
                "id": name,
                "audio_path": str(path.relative_to(root)),
                "audio_sha256": digest(path),
                "reference": "",
                "split": "diagnostic_unlabeled",
                "condition": name,
                "source_start_frame": begin,
                "source_end_frame": stop,
                "payload_pcm16_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    write_jsonl(output / "reproduction.jsonl", diagnostic)
    plan = {
        "kind": "opt0_pilot_inputs",
        "kss_manifest_sha256": manifest_sha,
        "failed_predictions_sha256": digest(failed_predictions),
        "kss_rank_indices_zero_based": ranks,
        "baseline_inputs": [
            {"id": s.id, "frames": len(audio[s.id].samples), "audio_sha256": s.audio_sha256}
            for s in selected
        ],
        "reproduction_inputs": [
            {
                k: r[k]
                for k in ("id", "source_start_frame", "source_end_frame", "payload_pcm16_sha256")
            }
            for r in diagnostic
        ],
        "baseline_manifest_sha256": digest(output / "baseline.jsonl"),
        "reproduction_manifest_sha256": digest(output / "reproduction.jsonl"),
        "tail_matches_original_payload": True,
    }
    write_json(output / "plan.json", plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd()
    prepare(
        root,
        root / "data/evaluation/kss-dev50/manifest.jsonl",
        root
        / "results/comparisons/file-validation-20261106-01/offline-prospective-qwen3_asr-t2-01/predictions.jsonl",
        args.output.resolve(),
    )


if __name__ == "__main__":
    main()
