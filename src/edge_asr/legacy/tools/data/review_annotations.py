"""사람의 검수 결정 JSONL을 원본 주석과 분리해 검증·반영한다. 자동 승인하지 않는다."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.vad_annotations import load_annotations, validate_annotation


def apply_reviews(rows, decisions):
    result = deepcopy(rows)
    by_id = {r["id"]: r for r in result}
    seen = set()
    allowed = {
        "id",
        "audio_sha256",
        "annotation_status",
        "reviewer",
        "reviewed_at",
        "review_method",
        "review_note",
        "regions",
        "endpoint_reference_frame",
        "exclusion_reason",
    }
    if not decisions:
        raise ValueError("검수 결정이 비어 있습니다.")
    for decision in decisions:
        key = decision.get("id")
        if key in seen or key not in by_id or set(decision) - allowed:
            raise ValueError("검수 ID 중복/미등록/허용되지 않은 필드")
        row = by_id[key]
        if decision.get("audio_sha256") != row["audio_sha256"] or decision.get(
            "annotation_status"
        ) not in ("human_reviewed", "excluded"):
            raise ValueError("WAV 해시와 명시적 검수 판정이 필요합니다.")
        row.update(decision)
        validate_annotation(row)
        seen.add(key)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--annotations", type=Path, required=True)
    p.add_argument("--decisions", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    rows, sha = load_annotations(a.annotations, a.root)
    if a.decisions is None:
        if a.output:
            p.error("--output은 --decisions와 함께 사용하세요.")
        print(json.dumps({"validated": len(rows), "annotation_sha256": sha}))
        raise SystemExit(0)
    if a.output is None:
        p.error("새 --output 폴더가 필요합니다.")
    output = a.output.resolve()
    if not output.is_relative_to(a.root.resolve() / "data") or output.exists():
        p.error("data/ 아래의 새 출력 폴더가 필요합니다.")
    decisions = [
        json.loads(l) for l in a.decisions.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    result = apply_reviews(rows, decisions)
    output.mkdir(parents=True, exist_ok=False)
    path = output / "annotations.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in result), encoding="utf-8"
    )
    (output / "provenance.json").write_text(
        json.dumps(
            {
                "source_annotation_sha256": sha,
                "decisions_sha256": digest(a.decisions),
                "files": {"annotations.jsonl": digest(path)},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"rows": len(result), "decisions_applied": len(decisions), "output": str(output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
