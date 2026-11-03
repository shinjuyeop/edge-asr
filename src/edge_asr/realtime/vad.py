"""1배속 입력의 실제 VAD 방출을 유한 Refine 큐에 전달하는 측정 경로."""

import time
from array import array

from edge_asr.realtime.replay import ConcurrentReplay
from edge_asr.segmentation import SegmentPipeline

POLICIES = ("fixed5", "vad0", "vad-continuation200")


class TimedProbability:
    """512프레임 호출만 따로 누적한다. reset 시 누적 계측은 유지한다."""

    def __init__(self, probability):
        self.probability = probability
        self.wall_s = self.cpu_s = 0.0
        self.calls = 0

    def reset(self):
        self.probability.reset()

    def __call__(self, samples):
        began, cpu = time.perf_counter(), time.process_time()
        try:
            return self.probability(samples)
        finally:
            self.wall_s += time.perf_counter() - began
            self.cpu_s += time.process_time() - cpu
            self.calls += 1


class OnlineVadReplay(ConcurrentReplay):
    """Zip 청크 분할은 고정 대조군과 같다. VAD는 원본별 100ms로 재조립한다."""

    def __init__(self, *args, vad, policy, convert=list, progress=None):
        if policy not in POLICIES[1:]:
            raise ValueError("온라인 VAD 정책이 필요합니다.")
        super().__init__(*args)
        self.vad, self.policy, self.convert = vad, policy, convert
        self.progress = progress
        self.pending = array("f")
        self.pending_peak = self.fed = 0
        self.pipeline = None
        self.vad_sources = []

    def feed_refine(self, block, stop, source):
        if self.pipeline is None:
            selection = "continuation" if self.policy == "vad-continuation200" else "all"
            self.pending = array("f")
            self.pending_peak = self.fed = 0
            self.active = {
                "id": source["id"],
                "start": source["start"],
                "end": source["end"],
                "status": "processing",
                "segments": [],
            }
            self.vad_sources.append(self.active)

            def emit(event, pcm):
                emitted = time.perf_counter_ns()
                self.active["segments"].append(dict(event))
                self.offer_payload(
                    array("f", (x / 32768 for x in pcm)),
                    source["start"] + event["owned_start_frame"],
                    source["start"] + event["owned_end_frame"],
                    source,
                    {
                        "context_start": source["start"] + event["context_start_frame"],
                        "context_end": source["start"] + event["context_end_frame"],
                        "emitted_ns": emitted,
                        "delivered_through_frame": self.delivered_through,
                        "segment": dict(event),
                    },
                )

            self.pipeline = SegmentPipeline(
                self.vad,
                "identity",
                5,
                emit,
                convert=self.convert,
                context_frames=3200 if selection == "continuation" else 0,
                context_selection=selection,
                native_budget_frames=None,
                progress=self.progress,
            )
        self.delivered_through = stop
        self.pending.extend(block)
        self.pending_peak = max(self.pending_peak, len(self.pending))
        began, cpu = time.perf_counter(), time.process_time()
        try:
            length = source["end"] - source["start"]
            while len(self.pending) >= 1600:
                pcm = self.pending[:1600]
                del self.pending[:1600]
                self.fed += 1600
                self.pipeline.accept(pcm, final=self.fed == length)
            if stop == source["end"]:
                if self.pending:
                    self.fed += len(self.pending)
                    self.pipeline.accept(self.pending, final=True)
                    self.pending = array("f")
                self.pipeline.finish()
                if self.fed != length:
                    raise ValueError("VAD 원본 프레임 누락")
                self.active["status"] = "success"
        finally:
            self.active["pipeline_wall_s"] = (
                self.active.get("pipeline_wall_s", 0) + time.perf_counter() - began
            )
            self.active["pipeline_cpu_s"] = (
                self.active.get("pipeline_cpu_s", 0) + time.process_time() - cpu
            )
            self.active["pipeline"] = self.pipeline.metadata()
            self.active["reassembly_peak_frames"] = self.pending_peak
        if stop == source["end"]:
            self.pipeline = None
