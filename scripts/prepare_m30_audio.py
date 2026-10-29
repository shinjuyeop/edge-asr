"""Host에서 KSS 음량 개발 자료 생성. 원본 WAV와 과거 manifest는 수정하지 않는다."""
from array import array
import argparse
import json
from pathlib import Path
import random
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edge_asr.audio_policy import POLICIES, preprocess
from edge_asr.benchmark import digest
from edge_asr.dataset import load_audio, load_manifest


def write_wav(path, integers):
    values = array("h", integers)
    if sys.byteorder != "little": values.byteswap()
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(values.tobytes())


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(root, output):
    root, output = root.resolve(), output.resolve()
    relative = output.relative_to(root)
    if relative.parts[0] != "data": raise ValueError("data/ 경로가 필요합니다.")
    if output.exists(): raise ValueError("기존 파생 입력을 덮어쓰지 않습니다.")
    samples, manifest_hash = load_manifest(root / "data/evaluation/kss-dev50/manifest.jsonl", root)
    sources = [(sample, load_audio(sample.audio_path, 16000)) for sample in samples]
    if any(sample.audio_sha256 != audio.sha256 for sample, audio in sources):
        raise ValueError("원본 KSS 해시 불일치")
    output.mkdir(parents=True)
    manifests = {policy: [] for policy in POLICIES}
    stats = []
    try:
        for sample, audio in sources:
            for condition in ("clean", "quiet24", "quiet24_noise55"):
                base = array("f", audio.samples)
                if condition != "clean":
                    base = array("f", (x * 10 ** (-24 / 20) for x in base))
                if condition.endswith("noise55"):
                    rng = random.Random("m30-noise-v1:" + sample.id)
                    noise = [rng.gauss(0, 1) for _ in base]
                    rms = (sum(x*x for x in noise) / len(noise)) ** .5
                    base = array("f", (x + n / rms * 10 ** (-55 / 20) for x, n in zip(base, noise)))
                # 모든 후보가 같은 PCM16 개발 입력을 받게 먼저 양자화한다.
                integers, _ = preprocess(base, "identity")
                base = array("f", (x / 32768 for x in integers))
                for policy in POLICIES:
                    directory = output / policy; directory.mkdir(exist_ok=True)
                    path = directory / f"{sample.id}-{condition}.wav"
                    started = time.perf_counter()
                    integers, metadata = preprocess(base, policy)
                    metadata["host_preprocessing_wall_s"] = time.perf_counter() - started
                    write_wav(path, integers)
                    record = {"id": f"{sample.id}-{condition}", "audio_path": str(path.relative_to(root)),
                              "audio_sha256": digest(path), "reference": sample.reference,
                              "split": "development", "condition": condition, "source_id": sample.id,
                              "source_sha256": audio.sha256, "audio_policy": policy}
                    manifests[policy].append(record)
                    stats.append({"id": record["id"], "condition": condition, **metadata})
            print(sample.id, flush=True)
        for policy, rows in manifests.items():
            (output / f"dev-{policy}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        write_json(output / "audio-stats.json", stats)
        value = {"schema_version": 1, "status": "complete", "name": "m30-kss-volume-v1",
                 "source_manifest_sha256": manifest_hash, "policies": POLICIES,
                 "conditions": {"clean": "원래 PCM 유지", "quiet24": "-24 dB 공통 감쇠 후 PCM16",
                                "quiet24_noise55": "-24 dB 음성 + 고정 seed Gaussian RMS -55 dBFS 후 PCM16"},
                 "selection": "공통 정책 1개. 새 clipping 0, 각 모델 clean CER 악화 <=1%p. 적격 중 두 모델/세 조건 평균 CER 최소, 동률 identity 우선. 외부 결과 참조 금지.",
                 "limitations": "개발용 합성 음량/잡음. 실제 소음·마이크 평가 아님. 정책 상태는 발화 시작에서 초기화. 기존 FLEURS 100개는 최종 독립 평가로 사용하지 않음.",
                 "license": "KSS: Kyubyong Park (2018), CC BY-NC-SA 4.0; 출처 docs/datasets.md",
                 "files": {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}}
        write_json(output / "provenance.json", value)
    except BaseException as error:
        write_json(output / "preparation-failed.json", {"error": repr(error), "status": "failed"})
        raise
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/m30-volume-dev"))
    args = parser.parse_args()
    value = prepare(args.root, args.root / args.output)
    print("파생 자료 준비:", len(value["files"]), "files")
