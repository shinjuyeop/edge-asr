"""모델 없이 교체 밖 원문 보존·토큰 앵커·정답 비의존·재생 정합성을 검증한다."""

import copy
import shutil
import sys
import unittest

import test_token_alignment as fixtures

from edge_asr.artifacts import digest
from edge_asr.token_alignment import map_job
from edge_asr.token_splice import TokenSplice
from edge_asr.tools.reports.splice_replay import evaluate, load_trace, replay

SHA = "a" * 64


def event(kind, at, **kwargs):
    return {"type": kind, "at_ns": at, "session_id": "session", **kwargs}


def job(index=0, start=0, end=16000, source="s"):
    return {
        "index": index,
        "source_id": source,
        "start": start,
        "end": end,
        "payload_float32_sha256": SHA,
        "accepted": True,
    }


def feed(engine, at, body, text="반가워"):
    engine.apply(event("job", at, job=body))
    engine.apply(event("receipt", at + 1, result={**body, "status": "success", "text": text}))


def candidate(engine, at, body, snapshot):
    mapping = map_job(
        snapshot,
        body,
        {
            "start": engine.sources[body["source_id"]].start,
            "end": engine.sources[body["source_id"]].end,
        },
    )
    return {
        "index": body["index"],
        "source_id": body["source_id"],
        "snapshot_index": snapshot["index"],
        **mapping,
    }


class TokenSpliceTests(unittest.TestCase):
    def engine(self, policy="guarded_replace_v1"):
        return TokenSplice("session", [{"id": "s", "start": 0, "end": 48000}], policy)

    def test_length_changing_replacement_uses_original_coordinates_and_preserves_future_suffix(
        self,
    ):
        e = self.engine()
        row = fixtures.snapshot()
        e.apply(event("snapshot", 1, snapshot=row))
        j = job()
        feed(e, 2, j)
        e.apply(event("candidate", 4, change=candidate(e, 4, j, row)))
        self.assertEqual(e.sources["s"].text, "반가워하세요")
        final = fixtures.snapshot(
            [" 안녕", " 하세요", " 다음"], [0.2, 1.2, 2.2], phase="final", index=1
        )
        e.apply(event("snapshot", 5, snapshot=final))
        r = e.finish(6)[0]
        self.assertEqual(r["text"], "반가워하세요다음")
        self.assertEqual(r["proof"]["retained_zip_characters"], 5)
        self.assertEqual(
            r["proof"]["retained_original_sha256"], r["proof"]["retained_output_sha256"]
        )
        self.assertEqual(r["proof"]["mechanical_missing_or_changed_zip_characters"], 0)

    def test_reverse_adjacent_jobs_never_shift_each_others_original_span(self):
        e = self.engine()
        row = fixtures.snapshot([" 안녕", " 하세요", " 다음"], [0.2, 1.2, 2.2])
        e.apply(event("snapshot", 1, snapshot=row))
        for at, j, text in ((2, job(1, 16000, 32000), "여러분"), (5, job(), "와")):
            feed(e, at, j, text)
            e.apply(event("candidate", at + 2, change=candidate(e, at + 2, j, row)))
        self.assertEqual(e.sources["s"].text, "와여러분다음")
        self.assertEqual(len(e.sources["s"].patches), 2)

    def test_overlap_and_duplicate_candidate_do_not_duplicate_replacement(self):
        e = self.engine()
        row = fixtures.snapshot()
        e.apply(event("snapshot", 1, snapshot=row))
        j = job()
        feed(e, 2, j)
        c = candidate(e, 4, j, row)
        e.apply(event("candidate", 4, change=c))
        e.apply(event("candidate", 5, change=c))
        self.assertEqual(e.ignored["duplicate_candidate"], 1)
        other = job(1)
        feed(e, 6, other, "다른말")
        e.apply(event("candidate", 8, change=candidate(e, 8, other, row)))
        self.assertEqual(e.sources["s"].text, "반가워하세요")
        self.assertEqual(e.decisions[-1]["reason"], "overlapping_range")

    def test_prefix_revision_revokes_patch_and_restores_new_zip_instead_of_old_text(self):
        e = self.engine()
        row = fixtures.snapshot()
        e.apply(event("snapshot", 1, snapshot=row))
        j = job()
        feed(e, 2, j)
        e.apply(event("candidate", 4, change=candidate(e, 4, j, row)))
        changed = fixtures.snapshot([" 새로운", " 말"], [0.2, 1.2], phase="final", index=1)
        e.apply(event("snapshot", 5, snapshot=changed))
        r = e.finish(6)[0]
        self.assertEqual(r["text"], changed["text"])
        self.assertEqual(r["active_patch_ids"], [])
        self.assertIn("source_invalid", [x["reason"] for x in e.decisions])

    def test_bad_anchor_and_diagnostic_revocation_fall_back_without_substring_search(self):
        for mode in ("anchor", "revoke"):
            e = self.engine()
            row = fixtures.snapshot()
            e.apply(event("snapshot", 1, snapshot=row))
            j = job()
            feed(e, 2, j)
            c = candidate(e, 4, j, row)
            if mode == "anchor":
                c["zip_span_text"] = "다른 원문"
            e.apply(event("candidate", 4, change=c))
            if mode == "revoke":
                e.apply(
                    event("candidate", 5, change={**c, "state": "held", "reasons": ["uncertain"]})
                )
            self.assertEqual(e.sources["s"].text, row["text"])
            self.assertFalse(e.sources["s"].patches)

    def test_failed_empty_dropped_and_missing_refine_never_change_zip(self):
        for case in ("failed", "empty", "dropped", "missing"):
            e = self.engine()
            row = fixtures.snapshot(phase="final")
            e.apply(event("snapshot", 1, snapshot=row))
            j = job()
            j["accepted"] = case != "dropped"
            e.apply(event("job", 2, job=j))
            if case != "missing":
                e.apply(
                    event(
                        "receipt",
                        3,
                        result={
                            **j,
                            "status": "failed" if case == "failed" else "success",
                            "text": "" if case == "empty" else "수정",
                        },
                    )
                )
            e.apply(event("candidate", 4, change=candidate(e, 4, j, row)))
            self.assertEqual(e.finish(5)[0]["text"], row["text"])

    def test_old_source_late_result_never_overwrites_the_active_source(self):
        e = TokenSplice(
            "session",
            [{"id": "s", "start": 0, "end": 48000}, {"id": "next", "start": 48000, "end": 96000}],
            "guarded_replace_v1",
        )
        row = fixtures.snapshot(phase="final")
        e.apply(event("snapshot", 1, snapshot=row))
        j = job()
        e.apply(event("job", 2, job=j))
        newer = fixtures.snapshot(index=1)
        newer.update(source_id="next", source_start=48000, end=96000)
        e.apply(event("snapshot", 3, snapshot=newer))
        e.apply(event("receipt", 4, result={**j, "status": "success", "text": "고침"}))
        e.apply(event("candidate", 5, change=candidate(e, 5, j, row)))
        self.assertEqual(e.sources["s"].text, "고침하세요")
        self.assertEqual(e.sources["next"].text, newer["text"])

    def test_session_identity_payload_and_unclosed_source_are_checked(self):
        e = self.engine()
        row = fixtures.snapshot()
        wrong = event("snapshot", 1, snapshot=row)
        wrong["session_id"] = "old"
        e.apply(wrong)
        self.assertIsNone(e.sources["s"].snapshot)
        e.apply(event("snapshot", 1, snapshot=row))
        j = job()
        e.apply(event("job", 2, job=j))
        with self.assertRaises(ValueError):
            e.apply(
                event(
                    "receipt",
                    3,
                    result={
                        **j,
                        "payload_float32_sha256": "b" * 64,
                        "status": "success",
                        "text": "수정",
                    },
                )
            )
        with self.assertRaises(ValueError):
            e.apply(event("candidate", 4, change={}, reference="정답"))
        with self.assertRaises(ValueError):
            e.finish(5)

    def test_literal_punctuation_is_not_cleaned_or_deduplicated(self):
        e = self.engine()
        row = fixtures.snapshot([" 안녕", " 하세요"], [0.2, 1.2])
        e.apply(event("snapshot", 1, snapshot=row))
        j = job()
        feed(e, 2, j, "안녕..")
        e.apply(event("candidate", 4, change=candidate(e, 4, j, row)))
        self.assertEqual(e.sources["s"].text, "안녕..하세요")


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("prlimit"), "Linux/prlimit 필요"
)
class SpliceReplayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TokenAlignmentCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        result, _ = self.fixture.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.native = self.root / "run"

    def test_full_replay_requires_no_assets_preserves_raw_and_rejects_overwrite(self):
        before = {
            str(p.relative_to(self.native)): digest(p)
            for p in self.native.rglob("*")
            if p.is_file()
        }
        shutil.rmtree(self.root / "models")
        for p in self.root.glob("*.wav"):
            p.unlink()
        r = replay(self.native, self.root / "replay")
        self.assertEqual(len(r["structural_candidate_job_ids"]), 2)
        self.assertEqual(r["policies"]["guarded_replace_v1"]["final_active_patches"], 2)
        self.assertEqual(
            r["policies"]["guarded_replace_v1"][
                "mechanical_missing_or_changed_unselected_zip_characters"
            ],
            0,
        )
        self.assertEqual(
            before,
            {
                str(p.relative_to(self.native)): digest(p)
                for p in self.native.rglob("*")
                if p.is_file()
            },
        )
        with self.assertRaises(FileExistsError):
            replay(self.native, self.root / "replay")
        with self.assertRaises(ValueError):
            replay(self.native, self.native / "nested")

    def test_gold_changes_scores_but_never_events_decisions_or_output(self):
        sources, worker, events, session, _ = load_trace(self.native)
        first = evaluate(sources, worker, events, session, "guarded_replace_v1")
        altered = copy.deepcopy(sources)
        for s in altered:
            s["reference"] = "다른 정답"
        second = evaluate(altered, worker, events, session, "guarded_replace_v1")
        self.assertEqual(first[1:4], second[1:4])
        self.assertNotEqual(first[0]["selected_cer"], second[0]["selected_cer"])

    def test_corrupted_raw_rejected_before_creating_output(self):
        path = self.native / "refine-results.jsonl"
        path.write_text(path.read_text() + "\n")
        with self.assertRaises(ValueError):
            replay(self.native, self.root / "bad")
        self.assertFalse((self.root / "bad").exists())
