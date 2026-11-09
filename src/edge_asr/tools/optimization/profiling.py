"""기존 ORT CPU 바이너리의 Run 경계와 Python 특징/디코딩 경계를 관찰한다."""

import argparse
import ctypes
import os
import time
from copy import deepcopy
from pathlib import Path

from edge_asr.adapters.qwen3 import Qwen3Adapter
from edge_asr.artifacts import digest
from edge_asr.tools.optimization.baseline import run


class Event(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("kind", "begin_ns", "end_ns", "failed")]


class Trace(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("abi", "enabled", "count", "overflow")] + [
        ("events", Event * 256)
    ]


class NativeTrace:
    def __init__(self, path):
        if str(path.resolve()) not in os.environ.get("LD_PRELOAD", "").split(":"):
            raise RuntimeError("프로세스 시작 시 계측 라이브러리를 LD_PRELOAD로 지정해야 합니다.")
        self.library = ctypes.CDLL(str(path))
        self.library.EdgeTraceInitialize.restype = ctypes.c_uint64
        self.library.EdgeTraceSessionMask.restype = ctypes.c_uint64
        self.library.EdgeTraceBegin.argtypes = [ctypes.c_uint64]
        self.library.EdgeTraceBegin.restype = None
        self.library.EdgeTraceEnd.restype = ctypes.POINTER(Trace)
        if self.library.EdgeTraceInitialize() != 1:
            raise RuntimeError("계측 ABI 초기화 실패")

    def begin(self, enabled):
        self.library.EdgeTraceBegin(int(enabled))

    def end(self):
        result = self.library.EdgeTraceEnd().contents
        if result.abi != 1 or result.count > 256 or result.overflow:
            raise RuntimeError("native 계측 ABI 또는 event 용량 오류")
        return [
            {name: getattr(event, name) for name in ("kind", "begin_ns", "end_ns", "failed")}
            for event in result.events[: result.count]
        ]

    def check_sessions(self):
        if self.library.EdgeTraceSessionMask() != 14:
            raise RuntimeError("Conv/Encoder/Decoder 세션 3개의 hook을 확인하지 못했습니다.")


def summarize(events, boundaries, began, ended, enabled):
    profile = {
        "schema_version": 1,
        "enabled": enabled,
        "inner_transcribe_s": (ended - began) / 1e9,
        "python_boundaries": boundaries,
        "ort_events": events,
        "ort_conv_s": None,
        "ort_encoder_s": None,
        "ort_prefill_s": None,
        "ort_token_decode_s": None,
        "ort_token_decode_calls": None,
        "native_non_ort_s": None,
        "decoder_inter_run_gap_s": None,
        "first_logits_ready_s": None,
        "first_decode_entry_s": None,
        "ttft_s": None,
    }
    if not enabled:
        if events:
            raise ValueError("비활성 계측에 native event가 있습니다.")
        return profile
    kinds = [event["kind"] for event in events]
    if len(kinds) < 3 or kinds[:3] != [1, 2, 3] or any(k != 4 for k in kinds[3:]):
        raise ValueError("예상한 CPU 세션 순서 Conv/Encoder/Prefill/Decode와 다릅니다.")
    previous = began
    for event in events:
        if event["failed"] or not previous <= event["begin_ns"] <= event["end_ns"] <= ended:
            raise ValueError("native event 오류 또는 단조 시계 경계 불일치")
        previous = event["end_ns"]
    durations = {
        k: sum((e["end_ns"] - e["begin_ns"]) / 1e9 for e in events if e["kind"] == k)
        for k in (1, 2, 3, 4)
    }
    decode = boundaries["decode_stream"]
    if events[0]["begin_ns"] < decode["begin_ns"] or events[-1]["end_ns"] > decode["end_ns"]:
        raise ValueError("ORT Run이 decode_stream 바깥에 있습니다.")
    profile.update(
        ort_conv_s=durations[1],
        ort_encoder_s=durations[2],
        ort_prefill_s=durations[3],
        ort_token_decode_s=durations[4],
        ort_token_decode_calls=kinds.count(4),
        native_non_ort_s=decode["elapsed_s"] - sum(durations.values()),
        first_logits_ready_s=(events[2]["end_ns"] - began) / 1e9,
        first_decode_entry_s=(events[3]["begin_ns"] - began) / 1e9 if len(events) > 3 else None,
        decoder_inter_run_gap_s=sum(
            (right["begin_ns"] - left["end_ns"]) / 1e9
            for left, right in zip(events[2:], events[3:])
        ),
    )
    if profile["native_non_ort_s"] < 0:
        raise ValueError("ORT 합계가 native decode_stream 시간보다 큽니다.")
    return profile


class Stream:
    def __init__(self, native, owner):
        self.native, self.owner = native, owner

    def set_option(self, *args):
        return self.native.set_option(*args)

    def accept_waveform(self, *args):
        return self.owner.measure("accept_waveform", lambda: self.native.accept_waveform(*args))

    @property
    def result(self):
        return self.native.result


class Recognizer:
    def __init__(self, native, owner):
        self.native, self.owner = native, owner

    def create_stream(self):
        return Stream(self.owner.measure("create_stream", self.native.create_stream), self.owner)

    def decode_stream(self, stream):
        return self.owner.measure("decode_stream", lambda: self.native.decode_stream(stream.native))


class ProfiledAdapter:
    def __init__(self, model, rate, root, library, mode, *, trace_factory=NativeTrace):
        if mode not in ("on", "off", "abba"):
            raise ValueError("on/off/abba 계측 모드가 필요합니다.")
        self.inner = Qwen3Adapter(model, rate, root)
        self.model_files = self.inner.model_files
        bootstrap_started = time.perf_counter()
        self.trace = trace_factory(library)
        bootstrap_s = time.perf_counter() - bootstrap_started
        self.mode, self.enabled, self.boundaries = mode, False, {}
        self.profile_metadata = {
            "library_sha256": digest(library),
            "mode": mode,
            "ort_abi": "1.28.2/API28",
            "runtime_bootstrap_s": bootstrap_s,
            "bootstrap_scope": "계측/원본 ORT dlopen은 adapter.load 타이머 전에 실행. stock과 로딩 시간 직접 비교하지 않음.",
            "scope": "기존 CPU Session.Run 경계만 관측. 특징은 accept_waveform 경계. 전체 encoder/LLM 내부 시간 및 실제 첫 토큰 반환은 분리하지 않음.",
            "first_token_scope": "first_logits_ready는 prefill Run 종료. first_decode_entry는 첫 비EOS 토큰 샘플링 이후 다음 Run 진입. 실제 TTFT 아님.",
        }
        self.last_result_metadata = None

    @property
    def metadata(self):
        return {**self.inner.metadata, "profiling": self.profile_metadata}

    def load(self):
        self.inner.load()
        self.trace.check_sessions()
        self.inner.recognizer = Recognizer(self.inner.recognizer, self)

    def prepare_call(self, phase, source_id, repeat):
        self.enabled = self.mode == "on" or (
            self.mode == "abba" and phase == "steady" and repeat % 4 in (2, 3)
        )

    def measure(self, key, function):
        began = time.perf_counter_ns()
        try:
            return function()
        finally:
            ended = time.perf_counter_ns()
            self.boundaries[key] = {
                "begin_ns": began,
                "end_ns": ended,
                "elapsed_s": (ended - began) / 1e9,
            }

    def transcribe(self, audio):
        self.boundaries = {}
        self.last_result_metadata = None
        self.trace.begin(self.enabled)
        began = time.perf_counter_ns()
        original_error = None
        try:
            return self.inner.transcribe(audio)
        except BaseException as error:
            original_error = error
            raise
        finally:
            ended = time.perf_counter_ns()
            self.last_result_metadata = deepcopy(self.inner.last_result_metadata) or {}
            events = []
            try:
                events = self.trace.end()
                profile = summarize(events, self.boundaries, began, ended, self.enabled)
            except (ValueError, RuntimeError, KeyError) as error:
                profile = {
                    "enabled": self.enabled,
                    "error": str(error),
                    "ort_events": events,
                    "python_boundaries": self.boundaries,
                    "ttft_s": None,
                }
                self.last_result_metadata["profile"] = profile
                if original_error is None:
                    raise
            self.last_result_metadata["profile"] = profile

    def close(self):
        self.inner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-library", type=Path, required=True)
    parser.add_argument("--mode", choices=("on", "off", "abba"), default="abba")
    parser.add_argument("--protocol", choices=("baseline", "reproduction"), default="baseline")
    parser.add_argument("--repetitions", type=int, default=4)
    args = parser.parse_args()
    if time.get_clock_info("perf_counter").implementation != "clock_gettime(CLOCK_MONOTONIC)":
        parser.error("native/Python 단조 시계가 일치하는 Linux 환경이 필요합니다.")
    return run(
        Path.cwd(),
        args.config.resolve(),
        args.manifest.resolve(),
        args.output.resolve(),
        protocol=args.protocol,
        repetitions=args.repetitions,
        adapter_factory=lambda model, rate, root: ProfiledAdapter(
            model, rate, root, args.trace_library.resolve(), args.mode
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
