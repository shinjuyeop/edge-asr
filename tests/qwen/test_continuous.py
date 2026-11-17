"""제어 가능한 모델·시계로 최신 후보, 완료 알림, 확정 우선순위와 복구를 검사한다."""

import time
import unittest
from array import array
from queue import Queue
from threading import Condition, Event, RLock
from unittest.mock import patch

from edge_asr.qwen.realtime.continuous import ContinuousPolicy
from edge_asr.qwen.realtime.replay import argument_parser
from edge_asr.qwen.realtime.session import CaptionSession
from edge_asr.qwen.realtime.stability import content_updates
from edge_asr.qwen.realtime.worker import DecodeWorker
from edge_asr.vad_probability import BoundedProbabilityVad


class Probability:
    def reset(self):
        pass

    def __call__(self, samples):
        return 1.0 if any(samples) else 0.0


class ControlledModel:
    last_result_metadata = None

    def __init__(self):
        self.started, self.release = Queue(), Queue()

    def transcribe(self, audio):
        self.started.put(audio)
        value = self.release.get(timeout=4)
        if isinstance(value, Exception):
            raise value
        return value


class ContinuousTests(unittest.TestCase):
    def policy(self, **settings):
        now, events = [0.0], []
        p = ContinuousPolicy(
            Condition(RLock()),
            events.append,
            9600,
            12800,
            80000,
            3200,
            clock=lambda: now[0],
            **settings,
        )
        return p, now, events

    def session(self):
        model, events = ControlledModel(), []
        with patch.object(ContinuousPolicy, "start"):
            s = CaptionSession(
                model,
                BoundedProbabilityVad(Probability()),
                None,
                events.append,
                {"t0_monotonic_ns": time.perf_counter_ns()},
                partial_mode="continuous",
                partial_after_s=0.6,
            )
        s.continuous.observe({"temperature_mc": 60000, "current_limits": 0})

        def close():
            for _ in range(20):
                model.release.put("정리")
            s.close()

        self.addCleanup(close)
        return s, model, events

    def feed(self, s, until):
        while s.position < until:
            end = min(s.position + 1600, until)
            s.accept_at(s, array("f", [0.25] * (end - s.position)), s.position, end)

    def wait_results(self, s, n):
        with s.worker.condition:
            self.assertTrue(s.worker.condition.wait_for(lambda: len(s.results) >= n, timeout=3))

    def test_unlimited_fresh_candidates_and_one_snapshot_per_execution(self):
        s, model, events = self.session()
        for n, end in enumerate((11200, 25600, 40000, 54400), 1):
            self.feed(s, end)
            audio = model.started.get(timeout=2)
            self.assertLessEqual(len(audio.samples), end)
            model.release.put(f"갱신 {n}")
            self.wait_results(s, n)
        self.assertEqual(len([e for e in events if e["type"] == "started"]), 4)
        self.assertEqual(s.continuous.stats["snapshots"], 4)
        self.assertGreater(s.continuous.stats["candidate_updates"], 4)
        self.assertTrue(model.started.empty())
        self.assertLessEqual(len(s.pipeline.ring.samples), s.pipeline.ring.capacity)

    def test_completion_runs_latest_without_next_input_and_snapshot_is_immutable(self):
        s, model, events = self.session()
        self.feed(s, 11200)
        first = model.started.get(timeout=2)
        saved = bytes(first.samples)
        self.feed(s, 48000)
        self.assertEqual(s.continuous.candidate[2], s.pipeline.fed)
        self.assertEqual(s.continuous.stats["snapshots"], 1)
        self.assertTrue(model.started.empty())
        model.release.put("첫 결과")
        latest = model.started.get(timeout=2)
        self.assertEqual(len(latest.samples), s.pipeline.fed)
        self.assertEqual(bytes(first.samples), saved)
        model.release.put("최신 결과")
        self.wait_results(s, 2)
        self.assertTrue(model.started.empty())
        self.assertEqual(s.continuous.stats["pending_peak"], 1)

    def test_final_priority_and_next_segment_after_final_then_eof(self):
        s, model, events = self.session()
        resets = []
        model.reset_audio_feature_cache = lambda: resets.append(True)
        self.feed(s, 11200)
        model.started.get(timeout=2)
        self.feed(s, 96000)
        model.release.put("이전 부분")
        final = model.started.get(timeout=2)
        self.assertEqual(len(final.samples), 80000)
        model.release.put("확정")
        following = model.started.get(timeout=2)
        self.assertEqual(len(following.samples), s.pipeline.fed - 80000)
        model.release.put("새 부분")
        self.wait_results(s, 3)
        model.release.put("다음 확정")
        self.assertEqual(s.finish_stream(s), "확정 다음 확정")
        self.assertEqual([r["kind"] for r in s.results], ["partial", "final", "partial", "final"])
        self.assertTrue(
            any(
                e["type"] == "partial_candidate_discarded" and e["reason"] == "final"
                for e in events
            )
        )
        self.assertFalse(s.worker.thread.is_alive())
        self.assertEqual(len(resets), 4)
        with self.assertRaisesRegex(RuntimeError, "종료된"):
            s.worker.submit("partial", 3, 0, 1, array("f", [1]), 1)

    def test_empty_partial_preserves_caption_recovers_only_on_new_audio_and_final_fails(self):
        s, model, _ = self.session()
        self.feed(s, 11200)
        model.started.get(timeout=2)
        model.release.put("남아야 하는 자막")
        self.wait_results(s, 1)
        self.feed(s, 25600)
        model.started.get(timeout=2)
        model.release.put("")
        self.wait_results(s, 2)
        self.assertEqual(s.text(), "남아야 하는 자막")
        self.assertEqual(s.results[-1]["failure_kind"], "empty_partial")
        self.assertFalse(s.results[-1]["displayed"])
        self.assertTrue(model.started.empty())
        self.feed(s, 40000)
        model.started.get(timeout=2)
        model.release.put("복구된 자막")
        self.wait_results(s, 3)
        self.assertEqual(s.text(), "복구된 자막")
        model.release.put(RuntimeError("실제 final 실패"))
        self.assertEqual(s.finish_stream(s), "")
        self.assertEqual(s.results[-1]["status"], "failed")
        self.assertTrue(s.results[-1]["displayed"])

    def test_same_length_deletion_stale_tokens_and_final_are_revision_safe(self):
        s, _, events = self.session()
        base = {
            "kind": "partial",
            "segment_id": 0,
            "start_frame": 0,
            "end_frame": 16000,
            "status": "provisional",
            "started_monotonic_s": time.perf_counter(),
            "received_monotonic_s": time.perf_counter(),
            "native_elapsed_s": 0.1,
        }

        def token(job, index, text):
            s._event(
                {**base, "type": "token", "job_id": job, "token_index": index, "hypothesis": text}
            )

        token(0, 1, "한국")
        token(0, 2, "한글")
        token(0, 3, "한")
        self.assertEqual(s.text(), "한")
        token(1, 1, "한국어")
        token(0, 4, "낡은 결과")
        token(1, 1, "순서가 지난 토큰")
        self.assertEqual(s.text(), "한국어")
        now = time.perf_counter()
        s._event(
            {
                **base,
                "type": "result",
                "job_id": 2,
                "kind": "final",
                "status": "success",
                "hypothesis": "확정",
                "submitted_monotonic_s": now,
                "ended_monotonic_s": now,
            }
        )
        token(3, 1, "확정 뒤 낡은 토큰")
        self.assertEqual(s.text(), "확정")
        self.assertEqual(sum(e.get("stale_revision", False) for e in events), 3)

    def test_hysteresis_missing_telemetry_sticky_bits_and_bounded_tail(self):
        p, now, events = self.policy()
        with p.condition:
            p.update(0, 0, 16000, lambda a, b: array("h", [1]) * (b - a))
            self.assertIsNone(p.choose())
            p.observe({"temperature_mc": 60000, "current_limits": 0, "history_limits": 15})
            self.assertIsNotNone(p.choose())
            p.update(0, 0, 32000, lambda a, b: array("h", [1]) * (b - a))
            now[0] = 1
            p.observe({"temperature_mc": 82000, "current_limits": 0})
            self.assertIsNone(p.choose())
            now[0] = 3
            p.observe({"temperature_mc": 79000, "current_limits": 0})
            self.assertIsNone(p.choose())
            now[0] = 5
            p.observe({"temperature_mc": 78000, "current_limits": 0})
            self.assertIsNotNone(p.choose())
            self.assertEqual(p.stats["thermal_backoff_s"], 4)
            p.update(0, 0, 78000, lambda a, b: array("h", [1]) * (b - a))
            self.assertIsNone(p.choose())
            self.assertEqual(events[-1]["reason"], "near_final")
            p.observe({"temperature_mc": None, "current_limits": None})
            self.assertTrue(p.hot)
            p.discard("final", 0)
            self.assertIsNone(p.candidate)

    def test_content_metric_counts_corrections_but_not_repeated_strings(self):
        events = [
            {
                "type": "token",
                "displayed": True,
                "segment_id": 0,
                "hypothesis": text,
                "published_s": at,
                "end_frame": 16000,
            }
            for at, text in [(1, "한국"), (1.1, "한국"), (1.5, "한글"), (2, "한")]
        ]
        result = content_updates(events, [{"owned_start_frame": 0, "owned_end_frame": 48000}])
        self.assertEqual(result["changed_events"], 3)
        self.assertEqual(result["interval_s"]["p50"], 0.5)

    def test_pace_thermal_limits_allow_updates_but_voltage_missing_and_extreme_heat_wait(self):
        p, _, _ = self.policy(thermal_policy="pace")
        with p.condition:
            p.observe({"temperature_mc": 85000, "current_limits": 8})
            p.update(0, 0, 9600, lambda a, b: b - a)
            self.assertIsNotNone(p.choose())  # 첫0.6초는 열 감속으로 미루지 않는다.
            p.update(0, 0, 22400, lambda a, b: b - a)
            self.assertIsNone(p.choose())  # 후속은 새 음성0.8 * 1.4초가 필요하다.
            p.update(0, 0, 28800, lambda a, b: b - a)
            self.assertIsNotNone(p.choose())
            for temperature, flags in ((85000, 1), (None, None), (90000, 6), (86000, 8)):
                p.observe({"temperature_mc": temperature, "current_limits": flags})
                self.assertTrue(p.hot)
            p.observe({"temperature_mc": 85000, "current_limits": 8, "history_limits": 15})
            self.assertFalse(p.hot)  # 78°C·현재 열 제한0을 기다리지 않는다.

    def test_pacing_charges_final_cancelled_work_and_keeps_debt_across_segments(self):
        p, now, events = self.policy(policy="paced", thermal_policy="pace")
        with p.condition:
            p.observe({"temperature_mc": 60000, "current_limits": 0})
            p.update(0, 0, 9600, lambda a, b: b - a)
            self.assertIsNotNone(p.choose())
            now[0] = 1
            p.completed(
                {
                    "kind": "partial",
                    "status": "cancelled",
                    "started_monotonic_s": 0,
                    "ended_monotonic_s": 1,
                }
            )
            p.update(0, 0, 25600, lambda a, b: b - a)
            self.assertIsNone(p.choose())
            self.assertEqual(events[-1]["reason"], "compute_pacing")
            now[0] += p.wait_s()
            self.assertIsNotNone(p.choose())
            started = now[0]
            now[0] += 2
            p.completed(
                {
                    "kind": "final",
                    "status": "failed",
                    "started_monotonic_s": started,
                    "ended_monotonic_s": now[0],
                }
            )
            p.update(1, 80000, 89600, lambda a, b: b - a)
            self.assertIsNone(p.choose())  # 새 구간이어도 과부하 누적을 지우지 않는다.
            now[0] += p.wait_s()
            self.assertIsNotNone(p.choose())  # 첫 요청만0.35초의 제한된 여유.
            self.assertAlmostEqual(p.stats["accounted_inference_s"], 3)

    def test_final_admission_is_bounded_even_with_large_cost_estimate(self):
        p, _, events = self.policy(policy="paced")
        with p.condition:
            p.observe({"temperature_mc": 60000, "current_limits": 0})
            p.update(0, 0, 64000, lambda a, b: b - a)
            self.assertIsNotNone(p.choose(estimated_s=10))
            p.update(0, 0, 76800, lambda a, b: b - a)
            self.assertIsNone(p.choose(estimated_s=10))
            self.assertEqual(events[-1]["reason"], "near_final")
            self.assertEqual(events[-1]["final_reserve_s"], 0.8)

    def test_worker_wakes_paced_candidate_without_input_or_thermal_notification(self):
        model, events, ready_at = ControlledModel(), [], time.perf_counter() + 0.05
        offered = False

        def offer():
            nonlocal offered
            if offered:
                return None
            if time.perf_counter() < ready_at:
                return ready_at - time.perf_counter()
            offered = True
            w.submit("partial", 0, 0, 1600, array("f", [0.25] * 1600), 1600)

        condition = Condition(RLock())
        with condition:
            w = DecodeWorker(model, events.append, condition=condition, next_partial=offer)
        try:
            self.assertEqual(len(model.started.get(timeout=2).samples), 1600)
            model.release.put("최신 결과")
            w.finish(2)
            self.assertEqual(len([e for e in events if e["type"] == "result"]), 1)
        finally:
            model.release.put("정리")
            w.abort()

    def test_thermal_recovery_wakes_latest_candidate_without_new_input(self):
        s, model, _ = self.session()
        s.continuous.observe({"temperature_mc": 83000, "current_limits": 8})
        self.feed(s, 32000)
        with s.worker.condition:
            self.assertIsNotNone(s.continuous.candidate)
            self.assertEqual(s.continuous.stats["snapshots"], 0)
        s.continuous.observe({"temperature_mc": 77000, "current_limits": 0})
        self.assertEqual(len(model.started.get(timeout=2).samples), s.pipeline.fed)
        model.release.put("회복")
        self.wait_results(s, 1)
        self.assertTrue(model.started.empty())

    def test_cli_preserves_default_and_rejects_conflicting_policy(self):
        required = ["--manifest", "input.jsonl", "--output", "new"]
        p = argument_parser()
        self.assertEqual(p.parse_args(required).partial_mode, "fixed")
        self.assertEqual(
            p.parse_args([*required, "--partial-mode", "continuous"]).partial_mode, "continuous"
        )
        for args in (
            {"adaptive_policy": "budgeted"},
            {"partial_followup_s": 1},
            {"partial_tail_margin_s": 2},
            {"continuous_policy": "unknown"},
            {"partial_thermal_policy": "unknown"},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                CaptionSession(None, None, 1, None, {}, partial_mode="continuous", **args)
        for args in ({"continuous_policy": "paced"}, {"partial_thermal_policy": "pace"}):
            with self.assertRaises(ValueError):
                CaptionSession(None, None, 1, None, {}, **args)

    def test_optional_cancellation_only_when_final_waits_and_preserves_final(self):
        class Streaming:
            supports_token_stream = True
            last_result_metadata = None

            def __init__(self):
                self.entered, self.continue_token = Event(), Event()

            def transcribe_stream(self, audio, emit):
                self.entered.set()
                if not self.continue_token.wait(2):
                    raise TimeoutError("fixture")
                emit({"hypothesis": "토큰", "token_index": 1, "native_elapsed_s": 0.1})
                return "확정 결과"

        for enabled, grace, age in ((False, 0, 0), (True, 0, 0), (True, 0.35, 0), (True, 0.35, 1)):
            with self.subTest(enabled=enabled, grace=grace, age=age):
                model, events = Streaming(), []
                w = DecodeWorker(
                    model,
                    events.append,
                    token_stream=True,
                    cancel_partial_for_final=enabled,
                    cancel_partial_grace_s=grace,
                )
                try:
                    pcm = array("f", [0.25] * 1600)
                    w.submit("partial", 0, 0, 1600, pcm, 1600)
                    self.assertTrue(model.entered.wait(2))
                    # 새 음성/부분 후보만으로는 취소하지 않는다.
                    w.submit("partial", 0, 0, 3200, pcm * 2, 3200)
                    self.assertFalse(
                        any(e["type"] == "partial_cancellation_requested" for e in events)
                    )
                    w.submit("final", 0, 0, 3200, pcm * 2, 3200)
                    with w.condition:
                        w.finals[0][0]["submitted_monotonic_s"] -= age
                    model.continue_token.set()
                    w.finish(3)
                    results = [e for e in events if e["type"] == "result"]
                    self.assertEqual(
                        [r["status"] for r in results],
                        ["cancelled" if enabled and age >= grace else "success", "success"],
                    )
                    self.assertEqual(results[-1]["hypothesis"], "확정 결과")
                finally:
                    model.continue_token.set()
                    w.abort()


if __name__ == "__main__":
    unittest.main()
