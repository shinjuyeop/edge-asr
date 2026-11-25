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
        jobs = []

        def prepare(job):
            jobs.append(dict(job))
            job["segment_id"] = -1  # 어댑터가 변경해도 작업자의 원본은 보존한다.

        model.prepare_realtime_job = prepare
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
        self.assertEqual([j["kind"] for j in jobs], ["partial", "final", "partial", "final"])
        self.assertEqual([j["segment_id"] for j in jobs], [0, 0, 1, 1])
        self.assertEqual([j["job_id"] for j in jobs], [r["job_id"] for r in s.results])
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

    def test_late_first_snapshot_caps_real_pcm_once_and_keeps_latest_candidate(self):
        p, _, events = self.policy(policy="first_text", first_snapshot_cap_s=1.2)
        calls = []

        def read(start, end):
            calls.append((start, end))
            return array("h", [1]) * (end - start)

        with p.condition:
            p.observe({"temperature_mc": 60000, "current_limits": 0})
            p.update(0, 1600, 46000, read)
            _, start, end, pcm = p.choose(0.7)
            self.assertEqual((start, end, len(pcm)), (1600, 20800, 19200))
            offered = next(e for e in events if e.get("reason") == "offered")
            self.assertEqual((offered["end_frame"], offered["candidate_end_frame"]), (20800, 46000))
            self.assertEqual(p.choose(0.9)[1:3], (1600, 46000))
            self.assertIsNone(p.choose(0.9))
            self.assertEqual(calls, [(1600, 20800), (1600, 46000)])
            self.assertEqual(p.stats["capped_first_snapshots"], 1)
            p.update(1, 50000, 127000, read)
            self.assertIsNone(p.choose(0.7))
            self.assertEqual(events[-1]["reason"], "near_final")
            p.discard("final", 1)
            self.assertIsNone(p.choose())

    def test_followup_wait_uses_visible_text_and_keeps_the_next_segment_first_free(self):
        p, now, events = self.policy(policy="first_text", partial_after_display_s=1.2)

        def read(a, b):
            return b - a

        with p.condition:
            p.observe({"temperature_mc": 60000, "current_limits": 0})
            p.update(0, 0, 16000, read)
            self.assertIsNotNone(p.choose(0.5))
            last = dict(
                kind="partial",
                status="success",
                displayed=True,
                hypothesis="안녕",
                segment_id=0,
                start_frame=0,
                end_frame=16000,
                ended_monotonic_s=0.1,
            )
            p.update(0, 0, 36000, read)
            self.assertIsNone(p.choose(0.5, previous=last))
            self.assertEqual(events[-1]["reason"], "after_partial_display")
            self.assertAlmostEqual(p.wait_s(), 1.3)
            now[0] = 1.31
            self.assertIsNotNone(p.choose(0.5, previous=last))
            p.discard("final", 0)
            p.update(1, 50000, 66000, read)
            self.assertIsNotNone(p.choose(0.5, previous=last))
        with self.assertRaises(ValueError):
            self.policy(policy="latest", partial_after_display_s=1.2)

    def test_first_snapshot_cap_leaves_early_request_intact(self):
        p, _, _ = self.policy(policy="first_text", first_snapshot_cap_s=1.2)
        with p.condition:
            p.observe({"temperature_mc": 60000, "current_limits": 0})
            p.update(0, 0, 25000, lambda a, b: b - a)
            self.assertEqual(p.choose(0.7)[1:], (0, 25000, 25000))
            self.assertIsNone(p.candidate)
        with self.assertRaises(ValueError):
            self.policy(policy="headroom", first_snapshot_cap_s=1.2)

    def test_first_text_uses_observed_meaningful_latency_and_reserves_actual_remaining_input(self):
        cv = Condition()
        events = []
        p = ContinuousPolicy(cv, events.append, 7200, 10_400, 80_000, 2400, policy="first_text")
        with cv:
            p.observe({"temperature_mc": 78000, "current_limits": 0})
            p.update(0, 0, 8000, lambda a, b: b - a)
            self.assertIsNotNone(p.choose())
            sample = {
                "segment_id": 0,
                "end_frame": 8000,
                "kind": "partial",
                "displayed": True,
                "status": "provisional",
                "job_id": 7,
                "started_monotonic_s": 10,
            }
            p.record_display({**sample, "hypothesis": ".", "published_s": 0.20}, None, 10)
            self.assertIsNone(p.first_display)
            p.record_display({**sample, "hypothesis": "첫", "published_s": 0.45}, None, 10)
            p.completed(
                {
                    **sample,
                    "status": "success",
                    "start_frame": 0,
                    "adapter_result": {
                        "native_result": {"audio_encode_s": 0.15, "audio_embedding_eval_s": 0.10}
                    },
                }
            )
            self.assertEqual(len(p.first_costs), 1)
            p.update(1, 80_000, 104_000, lambda a, b: b - a)
            self.assertIsNone(p.choose(estimated_s=4.2, available_frame=158_000))
            self.assertEqual(events[-1]["reason"], "near_final")
            # 새 구간에서 전체 완료 예측 4.2초보다 실제 첫 표시 추정이 짧으면 시작한다.
            p.update(2, 160_000, 184_000, lambda a, b: b - a)
            self.assertIsNotNone(p.choose(estimated_s=4.2, available_frame=185_000))
            self.assertLess(events[-1]["estimated_useful_text_s"], 4.2)
            eligible = [e for e in events if e["type"] == "partial_audio_eligible"]
            self.assertEqual([e["segment_id"] for e in eligible], [0, 1, 2])

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

    def test_budget_uses_fresh_audio_not_prefix_replay_and_preserves_first_priority(self):
        p, now, events = self.policy(policy="first_text_budget", thermal_policy="pace")
        with p.condition:
            p.observe({"temperature_mc": 65000, "current_limits": 0})
            p.update(0, 0, 16000, lambda a, b: b - a)
            self.assertIsNotNone(p.choose(1.4, pending_pause=True))
            first = {
                "kind": "partial",
                "status": "provisional",
                "segment_id": 0,
                "job_id": 1,
                "end_frame": 16000,
                "displayed": True,
                "started_monotonic_s": 0,
                "published_s": 0.5,
                "hypothesis": "한국어",
            }
            p.record_display(first, None, 0)
            p.record_display({**first, "end_frame": 24000, "hypothesis": "한국."}, first, 0)
            self.assertEqual(p.fresh_display, (0, 16000, 0.5))
            p.update(0, 0, 32000, lambda a, b: b - a)
            now[0] = 1.6
            self.assertIsNone(p.choose(1.4, pending_pause=True))
            self.assertEqual(events[-1]["reason"], "vad_pause_pending")
            self.assertIsNone(p.choose(1.4))
            self.assertEqual(events[-1]["reason"], "followup_compute_budget")
            now[0] = 3.2
            self.assertIsNotNone(p.choose(1.4))
            p.discard("final", 0)
            p.update(1, 64000, 80000, lambda a, b: b - a)
            p.observe({"temperature_mc": 90000, "current_limits": 8})
            self.assertIsNone(p.choose(0.5))
            self.assertEqual(events[-1]["reason"], "thermal_backoff")
            p.observe({"temperature_mc": 85000, "current_limits": 8})
            self.assertIsNotNone(p.choose(0.5))

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

    def test_headroom_keeps_latest_candidate_with_time_for_inference_and_margin(self):
        p, _, events = self.policy(policy="headroom", thermal_policy="pace")
        with p.condition:
            p.observe({"temperature_mc": 60000, "current_limits": 0})
            p.update(0, 0, 9600, lambda a, b: b)
            self.assertIsNotNone(p.choose())
            p.update(0, 0, 44800, lambda a, b: b)
            p.update(0, 0, 48000, lambda a, b: b)
            self.assertIsNone(p.choose(estimated_s=1.8))  # 2초 여유에 0.2초 여백 포함
            self.assertEqual(events[-1]["reason"], "near_final")
            self.assertEqual(p.choose(estimated_s=1.2)[-1], 48000)
            p.update(1, 80000, 147200, lambda a, b: b)
            self.assertIsNone(p.choose())  # 이력 없이 끝에 도달한 새 구간

    def test_followup_uses_displayed_prefix_time_and_current_input_once_per_segment(self):
        policy, _, events = self.policy(policy="headroom_followup")
        native = dict(
            first_generated_token_s=0.45,
            token_decode_s=0.20,
            audio_encode_s=0.24,
            audio_embedding_eval_s=0.06,
        )
        with policy.condition:
            policy.observe({"temperature_mc": 60000, "current_limits": 0})
            for segment, start in ((0, 0), (1, 80000)):
                previous = dict(
                    kind="partial",
                    status="success",
                    displayed=True,
                    segment_id=segment,
                    start_frame=start,
                    end_frame=start + 16000,
                    adapter_result={"native_result": native},
                )
                policy.update(segment, start, start + 16000, lambda a, b: b)
                self.assertIsNotNone(policy.choose(1))
                policy.update(segment, start, start + 48000, lambda a, b: b)
                for changes in (
                    {"displayed": False},
                    {"end_frame": start + 15000},
                    {"segment_id": segment - 1},
                    {"status": "cancelled"},
                    {"adapter_result": None},
                ):
                    self.assertIsNone(policy.choose(2.3, previous={**previous, **changes}))
                self.assertIsNone(
                    policy.choose(2.3, previous=previous, available_frame=start + 64000)
                )
                policy.observe({"temperature_mc": 83000, "current_limits": 0})
                self.assertIsNone(policy.choose(2.3, previous=previous))
                policy.observe({"temperature_mc": 60000, "current_limits": 0})
                self.assertIsNotNone(policy.choose(2.3, previous=previous))
                self.assertTrue(events[-1]["near_final_followup"])
                self.assertAlmostEqual(events[-1]["prefix_time_s"], 1.25)
                policy.update(segment, start, start + 60800, lambda a, b: b)
                self.assertIsNone(
                    policy.choose(2.3, previous={**previous, "end_frame": start + 48000})
                )
            self.assertEqual(policy.stats["near_final_followups"], 2)

    def test_projected_gap_uses_first_changed_display_with_a_new_audio_end(self):
        policy, now, events = self.policy(policy="headroom_followup", followup_gap_target_s=4.0)
        previous = dict(
            kind="partial",
            status="success",
            displayed=True,
            segment_id=0,
            start_frame=0,
            end_frame=16000,
            hypothesis="한글",
            adapter_result={
                "native_result": dict(
                    first_generated_token_s=0.45,
                    token_decode_s=0.2,
                    audio_encode_s=0.24,
                    audio_embedding_eval_s=0.06,
                )
            },
        )
        with policy.condition:
            policy.observe({"temperature_mc": 60000, "current_limits": 0})
            policy.update(0, 0, 16000, lambda a, b: b)
            self.assertIsNotNone(policy.choose(1))
            policy.update(0, 0, 48000, lambda a, b: b)
            now[0] = 10.5
            self.assertIsNone(policy.choose(2.3, previous=previous))
            event = {**previous, "published_s": 1.0}
            policy.record_display(event, None, 9.0)
            for changes in (
                {"end_frame": 8000},
                {"hypothesis": "한글"},
                {"displayed": False},
                {"status": "failed"},
                {"hypothesis": " "},
            ):
                policy.record_display(
                    {
                        **event,
                        "end_frame": 32000,
                        "hypothesis": "한글입니다",
                        "published_s": 1.4,
                        **changes,
                    },
                    previous,
                    9.0,
                )
            policy.record_display(
                {**event, "hypothesis": "수정", "published_s": 1.4}, previous, 9.0
            )
            self.assertEqual(policy.last_display_at, 10.0)
            self.assertIsNone(policy.choose(2.3, previous=previous))
            # 0.75초 경과 + 2초 남은 음성 + 문구 재생 1.25초가 정확히 기준에 도달한다.
            now[0] = 10.75
            self.assertIsNotNone(policy.choose(2.3, previous=previous))
            self.assertEqual(events[-1]["projected_gap_s"], 4.0)
            self.assertEqual(events[-1]["display_age_s"], 0.75)
            self.assertEqual(policy.stats["near_final_followups"], 1)
            policy.update(1, 80000, 96000, lambda a, b: b)
            self.assertEqual(policy.last_display_at, 10.0)
            policy.record_display(
                {**event, "segment_id": 1, "end_frame": 96000, "published_s": 2}, previous, 9.0
            )
            self.assertEqual(policy.last_display_at, 11.0)

    def test_projected_gap_requires_followup_policy_and_positive_target(self):
        for policy, target in (
            ("headroom", 4),
            ("headroom_followup", 0),
            ("headroom_followup", float("nan")),
            ("headroom_followup", float("inf")),
        ):
            with self.subTest(policy=policy, target=target), self.assertRaises(ValueError):
                self.policy(policy=policy, followup_gap_target_s=target)
        with self.assertRaises(ValueError):
            CaptionSession(None, None, 1, None, {}, followup_gap_target_s=4)

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
            {"continuous_policy": "headroom_followup"},
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
