"""기존 개발 자료를 참조하는 새 구간 검사 목록. 원본/과거 결과는 덮어쓰지 않는다."""
from array import array
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edge_asr.audio_policy import preprocess
from edge_asr.benchmark import digest
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.segmentation import pcm_bytes


def prepare(root, output):
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / "data"):
        raise ValueError("새 data 경로가 필요합니다.")
    samples, sha = load_manifest(root / "data/evaluation/m3-vad-20261029-01/identity.jsonl", root)
    output.mkdir(parents=True, exist_ok=False)
    old = root / "data/evaluation/m3-vad-20261029-01"
    paced_ids = {json.loads(s)["id"] for s in (old / "paced-identity.jsonl").read_text().splitlines()}
    paced_ids |= {"continuous-nine", "synthetic-long-30"}
    rows = []
    for sample in samples:
        audio = load_audio(sample.audio_path, 16000)
        if sample.audio_sha256 != audio.sha256:
            raise ValueError("기존 입력 해시 불일치")
        padded = array("f", [0] * 8000) + audio.samples + array("f", [0] * 16000)
        expected = {}
        for policy in ("identity", "rms26"):
            pcm, info = preprocess(padded, policy)
            expected[policy] = {"pcm_sha256": hashlib.sha256(pcm_bytes(pcm)).hexdigest(),
                                "newly_clipped_samples": info["newly_clipped_samples"]}
        rows.append({"id": sample.id, "audio_path": str(sample.audio_path.relative_to(root)),
                     "audio_sha256": audio.sha256, "reference": sample.reference,
                     "condition": sample.condition, "split": "development",
                     "source_frames": len(audio.samples), "padded_frames": len(padded),
                     "expected_processed": expected})
    if not paced_ids.issubset({r['id'] for r in rows}):
        raise ValueError("계획된 1배속 자료가 없습니다.")
    for name, selected in (("all", rows), ("paced", [r for r in rows if r["id"] in paced_ids])):
        (output / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in selected))
    (output / "component-spans.json").write_bytes((old / "component-spans.json").read_bytes())
    provenance = {"source_manifest_sha256": sha, "cases": len(rows), "paced_cases": len(paced_ids),
                  "policies": ["identity", "rms26"], "hard_limits_s": [3, 5, 8], "paced_hard_limit_s": 5,
                  "native_soft_limit_s": 5, "prefix_frames": 8000, "suffix_frames": 16000,
                  "scope": "기존 개발 회귀 자료. 앞/뒤 무음도 연속 전처리하며 이전 사전 생성 RMS WAV와 조건이 다름. 정책 최적화/최종 평가 아님.",
                  "files": {p.name: digest(p) for p in output.iterdir() if p.is_file()}}
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+"\n")
    return provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/m3-segments-20261029-01"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.root / args.output), ensure_ascii=False))
