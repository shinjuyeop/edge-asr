"""병합 결정은 정답 없이 검증하고, 채점은 결정 뒤에만 적용한다."""

import copy
import json
import shutil
import sys
import unittest

import test_concurrent_probe as fixtures

from edge_asr.artifacts import digest
from edge_asr.merging import Merger
from edge_asr.tools.reports.merge_replay import evaluate_policy, load_trace, replay

SHA = "a" * 64


def event(kind, at, source="a", **extra):
    return {"session_id": "session", "source_id": source, "type": kind, "at_ns": at, **extra}


def identity(index=0, start=0, end=8000, **extra):
    return {
        "job_id": index,
        "start": start,
        "end": end,
        "context_start": start,
        "context_end": end,
        "payload_sha256": SHA,
        **extra,
    }


class MergeTests(unittest.TestCase):
    def engine(self, policy="refine_complete"):
        m = Merger("session", policy)
        m.register("a", 0, 16000)
        return m

    def test_late_result_changes_its_source_and_never_the_new_source(self):
        m = self.engine()
        m.register("b", 16000, 32000)
        m.apply(event("partial", 1, end=8000, text="원문"))
        m.apply(event("job", 2, accepted=True, **identity()))
        m.apply(event("close", 3, end=16000, text="원문"))
        m.apply(event("partial", 4, "b", end=24000, text="새 발화"))
        m.apply(event("refine", 5, status="success", text="수정", **identity()))
        self.assertEqual(m.sources["a"].text, "수정")
        self.assertEqual(m.sources["b"].text, "새 발화")
        m.apply(event("close", 6, "b", end=32000, text="새 발화"))
        self.assertEqual([r["text"] for r in m.finish(7)], ["수정", "새 발화"])
        result = m.apply(event("partial", 8, end=16000, text="오래된 결과"))
        self.assertFalse(result["accepted"])
        self.assertEqual(m.sources["a"].text, "수정")

    def test_reversed_results_wait_for_close_and_join_in_owned_order(self):
        m = self.engine()
        m.apply(event("job", 1, accepted=True, **identity()))
        second = identity(1, 8000, 16000, context_start=4800)
        m.apply(event("job", 2, accepted=True, **second))
        m.apply(event("refine", 3, status="success", text="둘", **second))
        m.apply(event("close", 4, end=16000, text="임시"))
        self.assertFalse(m.sources["a"].sealed)
        m.apply(event("refine", 5, status="success", text="하나", **identity()))
        final = m.finish(6)[0]
        self.assertEqual(final["text"], "하나 둘")
        self.assertEqual(final["at_ns"], 5)
        self.assertEqual(sum(u["phase"] == "final" for u in m.updates), 1)

    def test_early_refine_does_not_assume_future_job_count_or_replace_prefix(self):
        m = self.engine()
        m.apply(event("partial", 1, end=8000, text="가"))
        m.apply(event("job", 2, accepted=True, **identity()))
        m.apply(event("refine", 3, status="success", text="첫 결과", **identity()))
        self.assertEqual(m.sources["a"].text, "가")
        m.apply(event("job", 4, accepted=True, **identity(1, 8000, 16000)))
        m.apply(event("partial", 5, end=16000, text="가나"))
        m.apply(event("refine", 6, status="success", text="둘째 결과", **identity(1, 8000, 16000)))
        self.assertEqual(m.sources["a"].text, "가나")
        m.apply(event("close", 7, end=16000, text="가나"))
        self.assertEqual(m.finish(8)[0]["text"], "첫 결과 둘째 결과")

    def test_normalized_agreement_changes_format_but_disagreement_keeps_zip(self):
        for refined, model in (("안녕 하세요.", "refine"), ("다른 말", "zip")):
            m = self.engine("agreement_only")
            m.apply(event("job", 1, accepted=True, **identity()))
            m.apply(event("refine", 2, status="success", text=refined, **identity()))
            m.apply(event("close", 3, end=16000, text="안녕하세요"))
            self.assertEqual(m.finish(4)[0]["model"], model)

    def test_drop_failure_empty_and_missing_are_explicit_zip_fallbacks(self):
        for case in ("drop", "failed", "empty", "missing", "none"):
            m = self.engine()
            if case != "none":
                m.apply(event("job", 1, accepted=case != "drop", **identity()))
            if case in ("failed", "empty"):
                m.apply(
                    event(
                        "refine",
                        2,
                        status="failed" if case == "failed" else "success",
                        text=None if case == "failed" else "",
                        **identity(),
                    )
                )
            m.apply(event("close", 3, end=16000, text="네"))
            final = m.finish(4)[0]
            expected = {
                "drop": "dropped_refine",
                "failed": "failed_refine",
                "empty": "empty_refine",
                "missing": "missing_refine",
                "none": "no_refine_jobs",
            }[case]
            self.assertEqual((final["text"], final["reason"]), ("네", expected))
            if case == "missing":
                self.assertFalse(
                    m.apply(event("refine", 5, status="success", text="늦은 말", **identity()))[
                        "accepted"
                    ]
                )
                self.assertEqual(m.sources["a"].text, "네")

    def test_session_job_source_duplicate_and_stale_partial_cannot_overwrite(self):
        m = self.engine()
        m.register("b", 16000, 32000)
        wrong = event("partial", 1, end=8000, text="오래된 말")
        wrong["session_id"] = "previous"
        self.assertFalse(m.apply(wrong)["accepted"])
        m.apply(event("partial", 1, end=10000, text="현재"))
        self.assertFalse(m.apply(event("partial", 2, end=8000, text="뒤로"))["accepted"])
        m.apply(event("job", 3, accepted=True, **identity()))
        self.assertFalse(
            m.apply(event("refine", 4, "b", status="success", text="잘못된 원본", **identity()))[
                "accepted"
            ]
        )
        m.apply(event("refine", 5, status="success", text="그", **identity()))
        self.assertFalse(
            m.apply(event("refine", 6, status="success", text="응", **identity()))["accepted"]
        )
        m.apply(event("close", 7, end=16000, text="현재"))
        self.assertEqual(m.sources["a"].text, "그")

    def test_invalid_geometry_hash_gold_and_unclosed_source_are_rejected(self):
        for bad in (identity(start=-1), identity(context_end=9000), identity(payload_sha256="bad")):
            m = self.engine()
            with self.assertRaises(ValueError):
                m.apply(event("job", 1, accepted=True, **bad))
        m = self.engine()
        m.apply(event("job", 1, accepted=True, **identity()))
        with self.assertRaises(ValueError):
            m.apply(event("job", 2, accepted=True, **identity(1, 4000, 12000)))
        with self.assertRaises(ValueError):
            m.apply(
                event("refine", 3, status="success", text="말", **identity(payload_sha256="b" * 64))
            )
        with self.assertRaises(ValueError):
            m.apply(event("partial", 4, end=16000, text="말", reference="정답"))
        with self.assertRaises(ValueError):
            m.finish(5)


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("prlimit"), "Linux/prlimit 필요"
)
class MergeReplayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConcurrentCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        completed, _ = self.fixture.run_fixture(1)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.native = self.root / "run"

    def test_replay_requires_no_models_or_audio_and_preserves_originals(self):
        before = {
            str(p.relative_to(self.native)): digest(p)
            for p in self.native.rglob("*")
            if p.is_file()
        }
        shutil.rmtree(self.root / "models")
        for p in self.root.glob("*.wav"):
            p.unlink()
        r = replay(self.native, self.root / "replay")
        self.assertEqual(r["source_count"], 2)
        self.assertEqual(set(r["policies"]), {"zip_only", "refine_complete", "agreement_only"})
        self.assertEqual(
            before,
            {
                str(p.relative_to(self.native)): digest(p)
                for p in self.native.rglob("*")
                if p.is_file()
            },
        )
        self.assertFalse(
            json.loads((self.root / "replay/execution.json").read_text())["native_asr_inference"]
        )
        with self.assertRaises(FileExistsError):
            replay(self.native, self.root / "replay")
        with self.assertRaises(ValueError):
            replay(self.native, self.native / "nested")

    def test_changing_gold_changes_scores_only_and_never_decisions_or_timeline(self):
        sources, w, events, z, returns, session = load_trace(self.native)
        first = evaluate_policy(sources, w, events, z, returns, session, "refine_complete")
        changed = copy.deepcopy(sources)
        for row in changed:
            row["reference"] = "다른 정답"
        second = evaluate_policy(changed, w, events, z, returns, session, "refine_complete")
        self.assertEqual(first[1:3], second[1:3])
        self.assertNotEqual(first[0]["selected_cer"], second[0]["selected_cer"])

    def test_corrupted_or_incomplete_native_is_rejected_before_new_output(self):
        path = self.native / "summary.json"
        s = json.loads(path.read_text())
        s["input_complete"] = False
        path.write_text(json.dumps(s))
        with self.assertRaises(ValueError):
            replay(self.native, self.root / "replay")
        self.assertFalse((self.root / "replay").exists())
