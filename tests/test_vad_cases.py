"""작은 합성 파일로 VAD 목록·연속 입력의 출처와 보존을 검사한다."""

import json
import tempfile
import unittest
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from edge_asr.tools.data import vad_cases as helper


class VadCasesTests(unittest.TestCase):
    def test_same_references_negative_labels_and_continuous_source_ranges(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dev = root / "data/evaluation/m30-volume-dev"
            dev.mkdir(parents=True)
            stress = root / "data/evaluation/m23-stress"
            stress.mkdir(parents=True)
            rows, short = [], []
            for i in range(3):
                for condition in ("clean", "quiet24", "quiet24_noise55"):
                    path = dev / f"s{i}-{condition}.wav"
                    helper.write_wav(path, array("h", [100 + i] * 1600))
                    row = {
                        "id": f"s{i}-{condition}",
                        "audio_path": str(path.relative_to(root)),
                        "audio_sha256": helper.digest(path),
                        "reference": f"문장{i}",
                        "condition": condition,
                    }
                    rows.append(row)
                    if condition == "clean":
                        short.append({**row, "id": f"s{i}"})
            write = lambda p, r: p.write_text(
                "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in r), encoding="utf-8"
            )
            write(dev / "dev-identity.jsonl", rows)
            write(stress / "short.jsonl", short)
            for n in (30, 60, 120):
                write(stress / f"long-{n}.jsonl", [{**short[0], "id": f"long-{n}"}])
            path = stress / "silence.wav"
            helper.write_wav(path, array("h", [0] * 1600))
            write(
                stress / "silence.jsonl",
                [
                    {
                        "id": "silence",
                        "audio_path": str(path.relative_to(root)),
                        "audio_sha256": helper.digest(path),
                        "reference": "",
                    }
                ],
            )
            before = {str(p): helper.digest(p) for p in root.rglob("*") if p.is_file()}
            out = root / "data/evaluation/vad"
            result = helper.prepare(root, out)
            a, _ = helper.load_manifest(out / "identity.jsonl", root)
            b, _ = helper.load_manifest(out / "rms26.jsonl", root)
            self.assertEqual(
                [(r.id, r.reference, r.condition) for r in a],
                [(r.id, r.reference, r.condition) for r in b],
            )
            negatives = [
                r for r in a if r.condition.startswith("noise_") or r.condition == "silence"
            ]
            self.assertTrue(all(r.reference == "" for r in negatives))
            self.assertEqual(len(negatives), 7)
            spans = json.loads((out / "component-spans.json").read_text())["continuous-nine"]
            self.assertEqual(len(spans), 9)
            self.assertTrue(
                all(b["start_frame"] - a["end_frame"] == 16000 for a, b in zip(spans, spans[1:]))
            )
            self.assertEqual(before, {p: helper.digest(Path(p)) for p in before})
            for path, sha in result["files"].items():
                self.assertEqual(helper.digest(out / path), sha)
            with self.assertRaises(FileExistsError):
                helper.prepare(root, out)


if __name__ == "__main__":
    unittest.main()
