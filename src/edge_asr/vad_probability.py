"""실험용: 같은 k2 Silero v4의 확률과 재귀 상태만 유지한다. 기존 VAD와 별도 경로다."""

import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from edge_asr.artifacts import digest
from edge_asr.vad import MODEL_SHA

RATE = 16000
WINDOW = 512


class K2SileroProbability:
    """기존 SHA의 16kHz k2 v4만 지원. 모델 1회 로딩, 창마다 h/c 상태를 이어간다."""

    def __init__(self, model):
        model = Path(model)
        if digest(model) != MODEL_SHA:
            raise ValueError("기존 검증한 k2 Silero 가중치만 지원합니다.")
        import numpy as np
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(model.resolve()), sess_options=options, providers=["CPUExecutionProvider"]
        )
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if [x.name for x in inputs] != ["x", "h", "c"] or [x.name for x in outputs] != [
            "prob",
            "new_h",
            "new_c",
        ]:
            raise ValueError("지원하지 않는 Silero 입출력 서명입니다.")
        if any(x.type != "tensor(float)" for x in [*inputs, *outputs]):
            raise ValueError("float32 Silero 입출력이 필요합니다.")
        self.np = np
        self.version = ort.__version__
        self.reset()

    def reset(self):
        self.h = self.np.zeros((2, 1, 64), dtype=self.np.float32)
        self.c = self.np.zeros((2, 1, 64), dtype=self.np.float32)

    def __call__(self, samples):
        if len(samples) != WINDOW or any(not math.isfinite(x) or not -1 <= x <= 1 for x in samples):
            raise ValueError("유한한 512프레임 float PCM이 필요합니다.")
        x = self.np.asarray(samples, dtype=self.np.float32).reshape(1, WINDOW)
        prob, h, c = self.session.run(
            ["prob", "new_h", "new_c"], {"x": x, "h": self.h, "c": self.c}
        )
        if h.shape != (2, 1, 64) or c.shape != (2, 1, 64) or prob.size != 1:
            raise ValueError("Silero 확률/재귀 상태 형태가 다릅니다.")
        if not self.np.isfinite(h).all() or not self.np.isfinite(c).all():
            raise ValueError("Silero 재귀 상태에 비유한 값이 있습니다.")
        value = float(prob.reshape(-1)[0])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("잘못된 Silero 음성 확률")
        self.h, self.c = h, c
        return value

    def metadata(self):
        return {
            "backend": "python_onnxruntime_k2_v4_experimental",
            "python_onnxruntime_version": self.version,
            "model_sha256": MODEL_SHA,
            "num_threads": 1,
            "providers": self.session.get_providers(),
            "recurrent_state_bytes": self.h.nbytes + self.c.nbytes,
            "scope": "가중치 동일. sherpa 네이티브 ORT와 동등성/성능은 별도 검증 필요.",
        }


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    @property
    def samples(self):
        # 길이만 제공한다. 실제 PCM은 SegmentPipeline의 유한 버퍼가 소유한다.
        return range(self.end - self.start)


class BoundedProbabilityVad:
    """파형 대신 상태·시간축만 보관한다. 소비되지 않은 완료 큐는 1개에서 실패한다.

    sherpa v2.0.1의 히스테리시스·가상 버퍼 시간축을 참고한다. Python ORT의
    확률 결과가 네이티브 ORT와 같은지는 실제 비교로 확인해야 한다.
    """

    max_audio_buffer_frames = 0

    def __init__(self, probability):
        self.probability = probability
        self.reset()

    def reset(self):
        self.probability.reset()
        self.fed = self.head = self.temp_start = self.temp_end = 0
        self.triggered = False
        self.start = None
        self.queue = deque()
        self.queue_peak = self.active_peak = 0
        self.closed = False

    def _classify(self, probability, threshold, silence):
        if probability > threshold and self.temp_end:
            self.temp_end = 0
        if probability > threshold and not self.temp_start:
            self.temp_start = self.fed
            return False
        if probability > threshold and self.temp_start and not self.triggered:
            if self.fed - self.temp_start < 1600:
                return False
            self.triggered = True
            return True
        if probability < threshold and not self.triggered:
            self.temp_start = self.temp_end = 0
            return False
        if probability > max(threshold - 0.15, 0.01) and self.triggered:
            return True
        if probability < threshold and self.triggered:
            if not self.temp_end:
                self.temp_end = self.fed
            if self.fed - self.temp_end < silence:
                return True
            self.temp_start = self.temp_end = 0
            self.triggered = False
        return False

    def accept_waveform(self, samples):
        if self.closed or self.queue:
            raise RuntimeError("종료된 세션이거나 완료 구간을 소비하지 않았습니다.")
        if len(samples) != WINDOW:
            raise ValueError("정확히 512프레임이 필요합니다.")
        value = self.probability(samples)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("유한한 0~1 확률이 필요합니다.")
        # 이전 wrapper처럼 다음 창 수신 전의 가상 버퍼 길이로 soft limit을 판단한다.
        long_active = self.fed - self.head > 5 * RATE
        threshold, silence = (0.9, 1600) if long_active else (0.5, 4800)
        self.fed += WINDOW
        speech = self._classify(value, threshold, silence)
        if speech:
            if self.start is None:
                self.start = max(self.head, self.fed - 2 * WINDOW - 1600)
            self.active_peak = max(self.active_peak, self.fed - self.start)
        else:
            if self.start is not None:
                end = self.fed - silence
                if end <= self.start:
                    raise RuntimeError("잘못된 VAD 끝 경계")
                self.queue.append(Span(self.start, end))
                self.queue_peak = max(self.queue_peak, len(self.queue))
                self.head = end
            else:
                self.head = max(self.head, self.fed - 2 * WINDOW - 1600)
            self.start = None

    def is_speech_detected(self):
        return self.start is not None

    @property
    def current_segment(self):
        if self.start is None:
            raise ValueError("활성 구간이 없습니다.")
        # v2.0.1의 CurrentSpeechSegment는 Tail()-start-1 프레임을 노출한다.
        # 자연 종료/EOF의 완료 구간 끝은 이 스냅샷과 별도다.
        return Span(self.start, self.fed - 1)

    def empty(self):
        return not self.queue

    @property
    def front(self):
        if not self.queue:
            raise ValueError("완료 구간이 없습니다.")
        return self.queue[0]

    def pop(self):
        if not self.queue:
            raise ValueError("완료 구간이 없습니다.")
        self.queue.popleft()

    def flush(self):
        if self.closed:
            raise ValueError("이미 종료한 세션입니다.")
        if self.start is not None:
            if self.queue:
                raise RuntimeError("완료 큐를 먼저 소비해야 합니다.")
            self.queue.append(Span(self.start, self.fed))
            self.queue_peak = max(self.queue_peak, len(self.queue))
        self.start = None
        self.closed = True

    def metadata(self):
        return {
            "backend": "bounded_probability_experimental",
            "retained_audio_frames": 0,
            "queue_capacity": 1,
            "queue_peak": self.queue_peak,
            "active_span_peak_frames": self.active_peak,
            "accepted_frames": self.fed,
            "closed": self.closed,
            "scope": "VAD 내부의 파형 축적만 0. 외부 PCM·모델 세션·입력 WAV 메모리는 별도.",
        }
