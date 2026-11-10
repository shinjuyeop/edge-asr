"""고정100개 원본을 보존하고 두 엔진이 공유할 해시 셔플 순서를 만든다."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.tools.data.fixed100 import verify

SEED = "edge-asr-fixed100-baseline-20261109-v1"
MANIFEST_SHA256 = "f693163f3d0d11a09873d3d5dbbd8a68aea6ebd1897cc051fd99dd076f8b76ab"


def prepare(root, output):
    source = root / "data/evaluation/edge100-20261109-01"
    manifest = source / "manifest.jsonl"
    if digest(manifest) != MANIFEST_SHA256:
        raise ValueError("고정100개 원본 manifest 해시 불일치")
    verify(root, source)
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    ordered = sorted(
        rows, key=lambda row: hashlib.sha256((SEED + "/" + row["id"]).encode()).hexdigest()
    )
    # 순서 이외의 모든 필드는 원본과 같다.
    if sorted(rows, key=lambda row: row["id"]) != sorted(ordered, key=lambda row: row["id"]):
        raise ValueError("입력100개 내용 변경")
    output.mkdir(parents=True, exist_ok=False)
    destination = output / "evaluation.jsonl"
    write_jsonl(destination, ordered)
    plan = {
        "input_id": "edge100-20261109-01",
        "source_manifest_sha256": MANIFEST_SHA256,
        "evaluation_manifest_sha256": digest(destination),
        "order_policy": "sha256(seed + '/' + source_id) ascending",
        "order_seed": SEED,
        "order": [row["id"] for row in ordered],
        "count": len(ordered),
        "total_audio_s": sum(row["frames"] for row in ordered) / 16000,
        "known_same_speaker_adjacent_pairs": sum(
            a["source"] == b["source"] == "zeroth" and a["speaker_id"] == b["speaker_id"]
            for a, b in zip(ordered, ordered[1:])
        ),
        "fleurs_speaker_identity": None,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip()
        ),
        "configs": {
            name: digest(root / name)
            for name in (
                "configs/onnx.toml",
                "configs/gguf.toml",
            )
        },
    }
    write_json(output / "plan.json", plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = prepare(Path.cwd(), args.output.resolve())
    print(
        json.dumps({k: plan[k] for k in ("count", "total_audio_s", "evaluation_manifest_sha256")})
    )


if __name__ == "__main__":
    main()
