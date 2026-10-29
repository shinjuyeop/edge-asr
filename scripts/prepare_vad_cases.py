"""기존 KSS·무음·장문과 새 합성 잡음/연속 입력으로 VAD 개발 목록을 만든다."""
from array import array
import argparse
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edge_asr.audio_policy import preprocess
from edge_asr.benchmark import digest
from edge_asr.dataset import load_audio, load_manifest
from prepare_m30_audio import write_wav, write_json


def prepare(root, output):
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / "data"): raise ValueError("새 data/ 경로가 필요합니다.")
    output.mkdir(parents=True, exist_ok=False)
    source_hashes, manifests = {}, {"identity": [], "rms26": []}
    components = {}

    def append(identifier, audio, reference, condition, *, identity_path=None, source=None):
        for policy in manifests:
            if policy == "identity" and identity_path is not None:
                path = identity_path
            else:
                path = output / policy / f"{identifier}.wav"; path.parent.mkdir(exist_ok=True)
                pcm, _ = preprocess(audio, policy); write_wav(path, pcm)
            manifests[policy].append({"id": identifier, "audio_path": str(path.relative_to(root)),
                                      "audio_sha256": digest(path), "reference": reference,
                                      "split": "development", "condition": condition,
                                      "audio_policy": policy, "source": source})

    def sources(path):
        samples, sha = load_manifest(root / path, root); source_hashes[path] = sha
        for sample in samples:
            audio = load_audio(sample.audio_path, 16000)
            if sample.audio_sha256 != audio.sha256: raise ValueError("기존 원본 해시 불일치")
            yield sample, audio

    for sample, audio in sources("data/evaluation/m30-volume-dev/dev-identity.jsonl"):
        append(sample.id, audio.samples, sample.reference, sample.condition, identity_path=sample.audio_path,
               source={"sha256": audio.sha256, "kind": "kss_development"})
    for kind in ("silence", "long-30", "long-60", "long-120"):
        for sample, audio in sources(f"data/evaluation/m23-stress/{kind}.jsonl"):
            append(sample.id, audio.samples, sample.reference, kind, identity_path=sample.audio_path,
                   source={"sha256": audio.sha256, "kind": "m23_stress"})
    for db in (-55, -45, -35):
        for seed in (0, 1):
            rng = random.Random(f"m3-vad-noise:{db}:{seed}")
            noise = [rng.gauss(0, 1) for _ in range(48000)]
            rms = (sum(v*v for v in noise) / len(noise))**.5
            pcm, _ = preprocess(array("f", (v/rms*10**(db/20) for v in noise)), "identity")
            append(f"noise-{abs(db)}-{seed}", array("f", (v/32768 for v in pcm)), "", f"noise_{db}dbfs")

    # 세 실제 짧은 발화를 서로 다른 음량 조건으로 반복. 중간에 gain/VAD를 reset하지 않는다.
    short = list(sources("data/evaluation/m23-stress/short.jsonl"))[:3]
    identity_by_id = {r["id"]: r for r in manifests["identity"]}
    joined = array("f", [0.0] * 16000); texts, spans = [], []
    for condition in ("clean", "quiet24", "quiet24_noise55"):
        for sample, original in short:
            # short 목록 ID는 원래 KSS ID에 접두사가 있을 수 있어 원본 해시로 연결한다.
            clean = next(r for r in manifests["identity"] if r["condition"] == "clean" and r["audio_sha256"] == original.sha256)
            source_id = clean["id"].removesuffix("-clean") + "-" + condition
            row = identity_by_id[source_id]; audio = load_audio(root / row["audio_path"], 16000)
            start = len(joined); joined.extend(audio.samples); end = len(joined)
            spans.append({"id": source_id, "start_frame": start, "end_frame": end, "reference": row["reference"]})
            texts.append(row["reference"]); joined.extend([0.0] * 16000)
    append("continuous-nine", joined, " ".join(texts), "continuous_speech_silence_transitions")
    components["continuous-nine"] = spans
    for policy, rows in manifests.items():
        (output / f"{policy}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in rows), encoding="utf-8")
        # 1배속 검사는 짧은 실제 원음 10개 + 모든 무음/잡음. 후보 길이는 5초로 사전 고정.
        short_ids = {a.sha256 for _, a in sources("data/evaluation/m23-stress/short.jsonl")}
        paced_ids = {r["id"] for r in manifests["identity"] if r["condition"] == "clean" and r["audio_sha256"] in short_ids}
        paced = [r for r in rows if r["id"] in paced_ids or r["condition"] == "silence" or r["condition"].startswith("noise_")]
        (output / f"paced-{policy}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in paced), encoding="utf-8")
    write_json(output / "component-spans.json", components)
    info = {"name": "m3-vad-develop-v1", "source_manifest_sha256": source_hashes,
            "samples_per_policy": len(manifests["identity"]), "policies": ["identity", "rms26"],
            "soft_max_candidates_s": [3, 5, 8], "paced_candidate_s": 5,
            "threshold": .5, "min_speech_s": .1, "min_silence_s": .3,
            "gate": "음성 파일 전체 누락 0, 무음/합성 잡음 오탐 0을 우선 확인. 둘 다 충족 시 원음 VAD 우선. hard max·단어 보존·음소 endpoint 합격은 별도 정렬 검증 필요.",
            "limitations": "기존 개발 자료/합성 잡음. 음소 정렬 없는 WAV 범위 라벨. 길이 1초 미만의 실제 짧은 응답·현장 소음의 대표성을 보장하지 않음.",
            "files": {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    write_json(output / "provenance.json", info)
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/m3-vad-20261029-01"))
    args = parser.parse_args(); print(json.dumps(prepare(args.root, args.root/args.output), ensure_ascii=False))
