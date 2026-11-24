"""100ms 공통 전처리 → 512프레임 VAD → 유한 PCM 버퍼와 구간 상한.

ASR/텍스트 병합은 수행하지 않는다. 활성 VAD의 native flush는 호출하지 않는다.
"""

import hashlib
import math
import time
from array import array

from edge_asr.audio_policy import BLOCK_FRAMES, AudioPreprocessor
from edge_asr.dataset import pcm_bytes

RATE = 16000
WINDOW = 512
SEGMENT_LIMITS_S = (3, 4, 5, 6, 8)


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
        return self.samples[start - self.start : end - self.start]


class SegmentPipeline:
    """세션당 새 객체. emit(row, PCM16)는 동기 호출이며 PCM 보관 책임은 호출자에게 있다.

    상한은 소유 구간에 적용한다. 왼쪽 문맥을 포함한 payload는 상한+200ms 이하다.
    VAD 음성 이외 프레임의 생략은 입력 드롭과 별개이며, 단어 보존을 보증하지 않는다.
    """

    def __init__(
        self,
        vad,
        policy,
        max_segment_s,
        emit,
        *,
        convert=list,
        context_frames=3200,
        context_selection="all",
        onset_context_max_gap_frames=None,
        native_budget_frames=30 * RATE,
        clock=time.perf_counter,
        progress=None,
        vad_policy=None,
        split_on_pause=False,
        boundary_target_s=None,
        boundary_energy_valley=False,
        boundary_lookback_s=None,
    ):
        if type(max_segment_s) is not int or max_segment_s not in SEGMENT_LIMITS_S:
            raise ValueError("소유 구간 상한은 3/4/5/6/8초여야 합니다.")
        if boundary_target_s is not None and (
            not split_on_pause
            or not math.isfinite(boundary_target_s)
            or not 0 < boundary_target_s < max_segment_s
        ):
            raise ValueError("쉼 경계 목표는 양수이며 소유 구간 상한보다 작아야 합니다.")
        if boundary_energy_valley and boundary_target_s is None:
            raise ValueError("에너지 경계에는 명시적인 쉼 경계 목표가 필요합니다.")
        if boundary_lookback_s is not None and (
            not math.isfinite(boundary_lookback_s) or not 0.2 <= boundary_lookback_s <= 1.2
        ):
            raise ValueError("강제 경계의 양측 음향 검토 범위는 0.2~1.2초여야 합니다.")
        if type(context_frames) is not int or not 0 <= context_frames <= 3200:
            raise ValueError("문맥은 0~3200프레임이어야 합니다.")
        if context_selection not in ("all", "onset", "continuation", "unowned_onset"):
            raise ValueError("문맥 적용 위치는 all/onset/continuation/unowned_onset이어야 합니다.")
        if onset_context_max_gap_frames is not None and (
            context_selection != "unowned_onset"
            or not context_frames
            or type(onset_context_max_gap_frames) is not int
            or onset_context_max_gap_frames < 1
        ):
            raise ValueError("쉼 상한에는 미소유 시작 문맥과 양의 프레임 수가 필요합니다.")
        if native_budget_frames is None:
            capacity = getattr(vad, "max_audio_buffer_frames", None)
            if type(capacity) is not int or capacity != 0:
                raise ValueError("파형 축적이 없는 VAD만 활성 예산 해제를 허용합니다.")
        elif (
            type(native_budget_frames) is not int
            or native_budget_frames < max_segment_s * RATE + WINDOW
        ):
            raise ValueError("native 예산은 소유 구간 상한+한 윈도 이상이어야 합니다.")
        self.vad, self.emit, self.convert = vad, emit, convert
        self.progress = progress
        self.processor = AudioPreprocessor(policy)
        self.vad_processor = (
            AudioPreprocessor(vad_policy)
            if vad_policy is not None and vad_policy != policy
            else None
        )
        self.vad_pending = array("h")
        self.vad_pcm_digest = hashlib.sha256()
        self.vad_clipped = 0
        self.vad_gain_peak = 1.0
        self.maximum = max_segment_s * RATE
        self.context = context_frames
        self.context_selection = context_selection
        self.onset_context_max_gap = onset_context_max_gap_frames
        self.previous_vad_owned_end = None
        if split_on_pause and not hasattr(vad, "resumed_pause_frames"):
            raise ValueError("쉼 경계에는 재개 직전의 무음 길이를 제공하는 VAD가 필요합니다.")
        self.split_on_pause = split_on_pause
        self.boundary_target = (
            round(boundary_target_s * RATE) if boundary_target_s is not None else None
        )
        self.boundary_energy_valley = boundary_energy_valley
        self.boundary_lookback = (
            round(boundary_lookback_s * RATE) if boundary_lookback_s is not None else 0
        )
        self.energy_peak = 0.0
        self.quiet_windows = 0
        self.pause_emissions = 0
        self.span_id = 0
        self.continuation = False
        self.native_budget = native_budget_frames
        # 활성 구간+왼쪽 문맥+VAD lookback/종료 대기+100ms 입력 여유.
        self.ring = PcmRing(self.maximum + context_frames + RATE)
        self.pending = array("h")
        self.fed = self.owned_end = self.segment_id = self.hard_emissions = 0
        self.native_start = None
        self.committed_tail_overhang = 0
        self.native_budget_exhausted = False
        self.closed = self.final_input = False
        self.clock = clock
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
        if self.vad_processor is not None:
            vad_pcm, vad_trace = self.vad_processor.process_block(samples, final=final)
            if (vad_trace["start_frame"], vad_trace["end_frame"]) != (
                trace["start_frame"],
                trace["end_frame"],
            ):
                raise RuntimeError("VAD/ASR 전처리 시간축이 다릅니다.")
            self.vad_pending.extend(vad_pcm)
            self.vad_pcm_digest.update(pcm_bytes(vad_pcm))
            self.vad_clipped += vad_trace["newly_clipped_samples"]
            self.vad_gain_peak = max(self.vad_gain_peak, vad_trace["gain"])
        self.pending_peak = max(self.pending_peak, len(self.pending))
        while len(self.pending) >= WINDOW:
            block = self.pending[:WINDOW]
            del self.pending[:WINDOW]
            vad_block = None
            if self.vad_processor is not None:
                vad_block = self.vad_pending[:WINDOW]
                del self.vad_pending[:WINDOW]
            self._window(block, vad_block)

    def payload_start(self, begin):
        if self.context_selection == "unowned_onset":
            if self.continuation:
                return begin
            if self.onset_context_max_gap is not None and (
                self.previous_vad_owned_end is None
                or begin - self.previous_vad_owned_end > self.onset_context_max_gap
            ):
                return begin
            return max(self.owned_end, begin - self.context)
        use_context = self.context_selection == "all" or (
            self.continuation == (self.context_selection == "continuation")
        )
        return max(0, begin - self.context) if use_context else begin

    def _emit(self, start, end, reason, *, boundary_evidence=None):
        native_end = end
        end = min(end, self.ring.end)
        begin = max(start, self.owned_end)
        if begin >= end:
            return
        if end - begin > self.maximum:
            raise ValueError("강제 구간 상한 위반")
        context_start = self.payload_start(begin)
        pcm = self.ring.read(context_start, end)
        row = {
            "segment_id": self.segment_id,
            "reason": reason,
            "raw_start_frame": start,
            "raw_end_frame": native_end,
            "owned_start_frame": begin,
            "owned_end_frame": end,
            "context_start_frame": context_start,
            "context_end_frame": end,
            "emitted_at_audio_frame": min(self.fed, self.ring.end),
            "available_input_frame": self.ring.end,
            "emitted_monotonic_s": self.clock(),
            "emitted_at_wall_s": None,
            "after_flush": reason == "eof",
            "endpoint_eligible": reason == "vad_end",
            "overlap_removed_frames": max(0, begin - start),
            "payload_frames": len(pcm),
            "payload_sha256": hashlib.sha256(pcm_bytes(pcm)).hexdigest(),
            "owned_pcm_sha256": hashlib.sha256(pcm_bytes(pcm[begin - context_start :])).hexdigest(),
        }
        if self.context_selection != "all":
            row.update(
                vad_span_id=self.span_id,
                is_continuation=self.continuation,
                applied_context_frames=(
                    begin - context_start
                    if self.context_selection == "unowned_onset"
                    else self.context
                    if self.continuation == (self.context_selection == "continuation")
                    else 0
                ),
            )
        if self.onset_context_max_gap is not None:
            row["previous_vad_owned_end_frame"] = self.previous_vad_owned_end
            row["vad_onset_gap_frames"] = (
                begin - self.previous_vad_owned_end
                if not self.continuation and self.previous_vad_owned_end is not None
                else None
            )
        if boundary_evidence is not None:
            row["boundary_evidence"] = boundary_evidence
        self.emit(row, pcm)
        self.payload_peak = max(self.payload_peak, len(pcm))
        self.segment_id += 1
        self.owned_end = end
        self.continuation = reason in ("hard_limit", "pause_before_limit")

    def _drain(self, reason):
        while not self.vad.empty():
            front = self.vad.front
            start = int(front.start)
            end = start + len(front.samples)
            # native 참조는 pop 전에 읽는다.
            self.vad.pop()
            if not 0 <= start < end <= self.fed:
                raise ValueError(f"VAD 구간 오류: start={start}, end={end}, fed={self.fed}")
            # 종료 무음 대기가 나중에 빠지면서 이미 확정한 tail보다 native 끝이 앞설 수 있다.
            # 확정 구간은 되돌리지 않으며 해당 여분을 계측한다. 음소 끝으로 해석하지 않는다.
            self.committed_tail_overhang += max(0, self.owned_end - end)
            self._emit(start, end, reason)
            # hard_limit·pause_before_limit은 같은 VAD 안의 분할이다. native 종료에서만 갱신한다.
            self.previous_vad_owned_end = self.owned_end
            self.native_start = None
            self.span_id += 1
            self.continuation = False
            self.energy_peak = 0.0
            self.quiet_windows = 0

    def _resumed_energy(self, block):
        # 원래 PCM의 최근 강세에 비해 두 창 이상 낮아졌다가 강도가 회복됐는지 본다.
        # 현재 창의 시작만 경계로 쓰므로 이미 부분 요청에 넘긴 프레임을 되감지 않는다.
        energy = sum(sample * sample for sample in block) / WINDOW
        self.energy_peak = max(energy, self.energy_peak * 0.93)
        if self.energy_peak and energy <= self.energy_peak * 0.075:
            self.quiet_windows += 1
            return False
        resumed = self.quiet_windows >= 2 and energy >= self.energy_peak * 0.12
        self.quiet_windows = 0
        return resumed

    def _hard_cut(self, limit):
        if not self.boundary_lookback:
            return limit
        # 상한까지 도착한 원음만 사용한다. 60ms 저에너지대 양쪽에서 강도가 회복된
        # 경우만 택해 계속 조용해지는 문장 끝을 국소 최솟값으로 잘못 고르지 않는다.
        step = RATE // 50
        begin = limit - self.boundary_lookback
        pcm = self.ring.read(begin, limit)
        power = [sum(x * x for x in pcm[i : i + step]) for i in range(0, len(pcm) - step + 1, step)]
        median = sorted(power)[len(power) // 2]
        best, cut = None, limit
        for i in range(3, len(power) - 3):
            energy = sum(power[i - 1 : i + 2]) / 3
            left = max(power[i - 3 : i - 1])
            right = max(power[i + 2 : i + 4])
            if (
                energy < 0.24 * median
                and energy < 0.4 * min(left, right)
                and (best is None or energy <= best)
            ):
                best, cut = energy, begin + i * step + step // 2
        return cut

    def _window(self, block, vad_block=None):
        if (
            self.native_budget is not None
            and self.native_start is not None
            and self.fed + WINDOW - self.native_start > self.native_budget
        ):
            self.native_budget_exhausted = True
            raise RuntimeError(
                "native_active_budget_exhausted: 활성 VAD 30초 예산; reset/재입력 없이 중단"
            )
        signal = block if vad_block is None else vad_block
        self.vad.accept_waveform(self.convert(array("f", (x / 32768 for x in signal))))
        self.fed += WINDOW
        self._drain("vad_end")
        if self.vad.is_speech_detected():
            # start만 사용한다. 현재 전체 samples를 Python list로 복사하지 않는다.
            start = int(self.vad.current_segment.start)
            if not 0 <= start < self.fed:
                raise ValueError("잘못된 활성 VAD 시작점")
            self.native_start = start
            active = self.fed - start
            self.native_active_peak = max(self.native_active_peak, active)
            if self.native_budget is not None and active > self.native_budget:
                raise ValueError("활성 VAD 버퍼 예산 위반")
            begin = max(start, self.owned_end)
            energy_resume = self.boundary_energy_valley and self._resumed_energy(block)
            if self.split_on_pause and (
                self.vad.resumed_pause_frames >= 3 * WINDOW or energy_resume
            ):
                # 재개한 현재 창 전체는 다음 구간 소유다. 이전 progress 시각보다 거슬러 가지 않는다.
                cut = self.fed - WINDOW
                target = (
                    self.maximum - RATE * 0.8
                    if self.boundary_target is None
                    else self.boundary_target
                )
                if target <= cut - begin <= self.maximum and cut < self.ring.end:
                    evidence = (
                        "vad_pause"
                        if self.vad.resumed_pause_frames >= 3 * WINDOW
                        else "energy_valley"
                    )
                    self._emit(begin, cut, "pause_before_limit", boundary_evidence=evidence)
                    self.pause_emissions += 1
                    begin = self.owned_end
            # native VAD 상태를 바꾸지 않고 도착한 공통 PCM의 소유 구간만 확정한다.
            while min(self.fed, self.ring.end) - begin >= self.maximum:
                limit = begin + self.maximum
                cut = self._hard_cut(limit)
                self._emit(
                    begin,
                    cut,
                    "hard_limit",
                    boundary_evidence="lookback_valley" if cut < limit else None,
                )
                self.hard_emissions += 1
                begin = self.owned_end
            if self.progress is not None:
                # 경계가 결정될 때까지 마지막 탐색 범위는 수정 가능한 PCM이다.
                end = min(self.fed, self.ring.end, begin + self.maximum - self.boundary_lookback)
                self.progress(begin, end, self.ring.read)

    def finish(self):
        if self.closed or self.ring.end == 0:
            raise ValueError("비어 있거나 이미 종료한 세션입니다.")
        if self.pending:
            block = self.pending
            self.pending = array("h")
            block.extend([0] * (WINDOW - len(block)))
            vad_block = None
            if self.vad_processor is not None:
                vad_block = self.vad_pending
                self.vad_pending = array("h")
                vad_block.extend([0] * (WINDOW - len(vad_block)))
            self._window(block, vad_block)
        self.vad.flush()
        self._drain("eof")
        self.closed = True
        return self.metadata()

    def metadata(self):
        result = {
            "complete": self.closed,
            "frames": self.ring.end,
            "processed_pcm_sha256": self.pcm_digest.hexdigest(),
            "newly_clipped_samples": self.clipped,
            "segments": self.segment_id,
            "hard_limit_emissions": self.hard_emissions,
            "owned_max_frames": self.maximum,
            "context_frames": self.context,
            "split_on_pause": self.split_on_pause,
            "pause_before_limit_emissions": self.pause_emissions,
            "ring_capacity_frames": self.ring.capacity,
            "ring_peak_frames": self.ring.peak,
            "pending_peak_frames": self.pending_peak,
            "native_active_peak_frames": self.native_active_peak,
            "native_budget_frames": self.native_budget,
            "native_budget_exhausted": self.native_budget_exhausted,
            "committed_tail_overhang_frames": self.committed_tail_overhang,
            "payload_peak_frames": self.payload_peak,
            "native_accepted_frames": self.fed,
            "actual_acoustic_endpoint_s": None,
            "word_preservation": None,
        }
        if self.context_selection != "all":
            result["context_selection"] = self.context_selection
        if self.boundary_target is not None:
            result["boundary_target_frames"] = self.boundary_target
            result["boundary_energy_valley"] = self.boundary_energy_valley
        if self.boundary_lookback:
            result["boundary_lookback_frames"] = self.boundary_lookback
        if self.onset_context_max_gap is not None:
            result["onset_context_max_gap_frames"] = self.onset_context_max_gap
        if self.vad_processor is not None:
            result.update(
                vad_preprocess=self.vad_processor.policy,
                vad_processed_pcm_sha256=self.vad_pcm_digest.hexdigest(),
                vad_newly_clipped_samples=self.vad_clipped,
                vad_gain_peak=self.vad_gain_peak,
            )
        return result


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


def validate_capture(meta, replay, expected, policy):
    if replay is not None and (
        replay.get("dropped_chunks") != 0 or replay.get("complete") is not True
    ):
        raise RuntimeError(
            "입력 드롭/미완료: 시간축을 압축한 구간을 유효 결과로 집계하지 않습니다."
        )
    want = expected["expected_processed"][policy]
    if (
        not meta["complete"]
        or meta["frames"] != expected["padded_frames"]
        or meta["processed_pcm_sha256"] != want["pcm_sha256"]
        or meta["newly_clipped_samples"] != want["newly_clipped_samples"]
    ):
        raise RuntimeError("Host에서 고정한 공통 PCM과 일치하지 않습니다.")
