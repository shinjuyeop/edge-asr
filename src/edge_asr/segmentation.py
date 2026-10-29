"""100ms 공통 전처리 → 512프레임 VAD → 유한 PCM 버퍼와 구간 상한.

ASR/텍스트 병합은 수행하지 않는다. sherpa 2.0.1의 flush는 신경망 reset이 아니다.
"""

from array import array
import hashlib
import sys
import time

from edge_asr.audio_policy import AudioPreprocessor, BLOCK_FRAMES

RATE = 16000
WINDOW = 512


def pcm_bytes(pcm):
    value = array("h", pcm)
    if sys.byteorder != "little":
        value.byteswap()
    return value.tobytes()


class PcmRing:
    """절대 프레임 좌표를 가진 PCM16 유한 버퍼. 사라진 범위는 오류로 거부한다."""
    def __init__(self, capacity):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("양의 정수 버퍼 용량이 필요합니다.")
        self.capacity = capacity
        self.start = self.end = self.peak = 0
        self.samples = array("h")

    def append(self, start, pcm):
        if start != self.end or not pcm or len(pcm) > self.capacity:
            raise ValueError("누락/중복 없는 연속 PCM 블록이 필요합니다.")
        remove = max(0, len(self.samples) + len(pcm) - self.capacity)
        if remove:
            del self.samples[:remove]
            self.start += remove
        self.samples.extend(pcm)
        self.end += len(pcm)
        self.peak = max(self.peak, len(self.samples))

    def read(self, start, end):
        if not self.start <= start <= end <= self.end:
            raise ValueError("요청한 PCM 범위가 버퍼에 없습니다.")
        return self.samples[start - self.start:end - self.start]


class SegmentPipeline:
    """세션당 새 객체. emit(row, PCM16)는 동기 호출이며 PCM 보관 책임은 호출자에게 있다.

    상한은 소유 구간에 적용한다. 왼쪽 문맥을 포함한 payload는 상한+200ms 이하다.
    VAD 음성 이외 프레임의 생략은 입력 드롭과 별개이며, 단어 보존을 보증하지 않는다.
    """
    def __init__(self, vad, policy, max_segment_s, emit, *, convert=list,
                 context_frames=3200, clock=time.perf_counter):
        if type(max_segment_s) is not int or max_segment_s not in (3, 5, 8):
            raise ValueError("소유 구간 상한은 3/5/8초여야 합니다.")
        if type(context_frames) is not int or not 0 <= context_frames <= 3200:
            raise ValueError("문맥은 0~3200프레임이어야 합니다.")
        self.vad, self.emit, self.convert = vad, emit, convert
        self.processor = AudioPreprocessor(policy)
        self.maximum = max_segment_s * RATE
        self.context = context_frames
        # 활성 구간+왼쪽 문맥+VAD lookback/종료 대기+100ms 입력 여유.
        self.ring = PcmRing(self.maximum + context_frames + RATE)
        self.pending = array("h")
        self.fed = self.owned_end = self.segment_id = self.hard_flushes = 0
        self.closed = self.final_input = False
        self.clock, self.epoch = clock, clock()
        self.pcm_digest = hashlib.sha256()
        self.clipped = 0
        self.native_active_peak = self.pending_peak = self.payload_peak = 0
        self.vad.reset()

    def accept(self, samples, *, final=False):
        if self.closed or self.final_input:
            raise ValueError("종료된 입력에 다시 PCM을 공급할 수 없습니다.")
        pcm, trace = self.processor.process_block(samples, final=final)
        self.final_input = final
        self.ring.append(trace["start_frame"], pcm)
        self.pcm_digest.update(pcm_bytes(pcm))
        self.clipped += trace["newly_clipped_samples"]
        self.pending.extend(pcm)
        self.pending_peak = max(self.pending_peak, len(self.pending))
        while len(self.pending) >= WINDOW:
            block = self.pending[:WINDOW]
            del self.pending[:WINDOW]
            self._window(block)

    def _drain(self, reason):
        while not self.vad.empty():
            front = self.vad.front
            start = int(front.start)
            end = start + len(front.samples)
            # native 참조는 pop 전에 읽는다.
            self.vad.pop()
            if not 0 <= start < end <= self.fed:
                raise ValueError("VAD가 잘못된 프레임 범위를 반환했습니다.")
            end = min(end, self.ring.end)
            begin = max(start, self.owned_end)
            if begin >= end:
                continue  # EOF의 가상 0 패딩/이미 소유한 겹침. 새 구간을 만들지 않는다.
            if end - begin > self.maximum:
                raise ValueError("강제 구간 상한 위반")
            context_start = max(0, begin - self.context)
            pcm = self.ring.read(context_start, end)
            row = {"segment_id": self.segment_id, "reason": reason,
                   "raw_start_frame": start, "raw_end_frame": end,
                   "owned_start_frame": begin, "owned_end_frame": end,
                   "context_start_frame": context_start, "context_end_frame": end,
                   "emitted_at_audio_frame": min(self.fed, self.ring.end),
                   "available_input_frame": self.ring.end,
                   "emitted_monotonic_s": self.clock(),
                   "emitted_at_wall_s": None,
                   "after_flush": reason != "vad_end",
                   "overlap_removed_frames": max(0, begin - start),
                   "payload_frames": len(pcm),
                   "payload_sha256": hashlib.sha256(pcm_bytes(pcm)).hexdigest(),
                   "owned_pcm_sha256": hashlib.sha256(pcm_bytes(pcm[begin-context_start:])).hexdigest()}
            self.emit(row, pcm)
            self.payload_peak = max(self.payload_peak, len(pcm))
            self.segment_id += 1
            self.owned_end = end

    def _window(self, block):
        self.vad.accept_waveform(self.convert(array("f", (x / 32768 for x in block))))
        self.fed += WINDOW
        self._drain("vad_end")
        if self.vad.is_speech_detected():
            # start만 사용한다. 현재 전체 samples를 Python list로 복사하지 않는다.
            start = int(self.vad.current_segment.start)
            if not 0 <= start < self.fed:
                raise ValueError("잘못된 활성 VAD 시작점")
            active = self.fed - start
            self.native_active_peak = max(self.native_active_peak, active)
            if active > self.maximum:
                raise ValueError("활성 VAD 버퍼가 상한을 넘었습니다.")
            # 다음 32ms를 받으면 초과하는 경우 지금 확정한다. 최대 511프레임 일찍 분할.
            if active + WINDOW > self.maximum:
                self.vad.flush()
                self.hard_flushes += 1
                self._drain("hard_limit")
                if self.vad.is_speech_detected():
                    raise ValueError("flush 후 VAD 활성 상태가 정리되지 않았습니다.")

    def finish(self):
        if self.closed or self.ring.end == 0:
            raise ValueError("비어 있거나 이미 종료한 세션입니다.")
        if self.pending:
            block = self.pending
            self.pending = array("h")
            block.extend([0] * (WINDOW - len(block)))
            self._window(block)
        self.vad.flush()
        self._drain("eof")
        self.closed = True
        return self.metadata()

    def metadata(self):
        return {"complete": self.closed, "frames": self.ring.end,
                "processed_pcm_sha256": self.pcm_digest.hexdigest(),
                "newly_clipped_samples": self.clipped, "segments": self.segment_id,
                "hard_flushes": self.hard_flushes, "owned_max_frames": self.maximum,
                "context_frames": self.context, "ring_capacity_frames": self.ring.capacity,
                "ring_peak_frames": self.ring.peak, "pending_peak_frames": self.pending_peak,
                "native_active_peak_frames": self.native_active_peak,
                "payload_peak_frames": self.payload_peak, "native_accepted_frames": self.fed,
                "actual_acoustic_endpoint_s": None, "word_preservation": None}


class SegmentReplayAdapter:
    """기존 독립 WAV 입력기를 재사용하기 위한 국소 shim. ASR 어댑터로 등록하지 않는다."""
    def __init__(self, factory):
        self.factory = factory
        self.pipeline = None

    def start_stream(self):
        self.pipeline = self.factory()
        return self.pipeline

    def accept_chunk(self, stream, samples):
        stream.accept(samples, final=len(samples) < BLOCK_FRAMES)
        return ""

    def finish_stream(self, stream):
        stream.finish()
        return ""
