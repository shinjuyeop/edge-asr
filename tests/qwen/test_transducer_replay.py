import time
import unittest

from edge_asr.qwen.realtime.transducer import TransducerSession


class TransducerReplayTests(unittest.TestCase):
    def test_endpoint_and_eof_commit_without_resetting_on_text_changes(self):
        class Adapter:
            def start_stream(self):
                self.stream = object()
                self.chunks = iter((("안녕", False), ("안녕", True), ("또", False)))
                self.resets = 0
                return self.stream

            def accept_chunk(self, stream, samples, decode):
                self_test.assertIs(stream, self.stream)
                decode(lambda state: self_test.assertIs(state, self.stream), stream)
                text, self.endpoint = next(self.chunks)
                return text

            def is_endpoint(self, stream):
                return self.endpoint

            def reset_endpoint(self, stream):
                self_test.assertIs(stream, self.stream)
                self.resets += 1

            def finish_stream(self, stream, decode):
                self_test.assertIs(stream, self.stream)
                return "또 봐"

        self_test = self
        adapter = Adapter()
        events = []
        session = TransducerSession(
            adapter, events.append, {"t0_monotonic_ns": time.perf_counter_ns()}
        )
        stream = session.start_stream()
        for start in range(3):
            session.accept_at(stream, [0], start, start + 1)
        self.assertEqual(session.finish_stream(stream), "안녕 또 봐")
        self.assertEqual(adapter.resets, 1)
        self.assertEqual(session.scheduler_stats["decode_steps"], 3)
        self.assertEqual([row["kind"] for row in events], ["partial", "final", "partial", "final"])
        self.assertEqual([row["reason"] for row in session.segments], ["native_endpoint", "eof"])
        self.assertEqual(events[-1]["owned_start_frame"], 2)
