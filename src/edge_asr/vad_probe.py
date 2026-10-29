"""VAD만의 오디오 시간축 진단. 실제 음소 정렬이나 실시간 지연으로 해석하지 않는다."""
from array import array
import time


def collect_segments(vad, samples, convert, *, window=512, rate=16000,
                     paced=False, telemetry=None, clock=time.perf_counter, sleep=time.sleep):
    if not samples or window < 1 or rate < 1: raise ValueError("입력/윈도/샘플레이트가 필요합니다.")
    vad.reset()
    segments = []
    total, owned_end, last_start = len(samples), 0, -1
    started = clock()
    timings = telemetry if telemetry is not None else {}
    timings.update(paced=paced, processing_wall_s=0.0, input_lateness_s=[], window_frames=window,
                   input_frames=total, native_accepted_frames=((total+window-1)//window)*window,
                   input_drops=None, backlog=None,
                   producer_scope="동일 스레드의 입력 시각 대기. 독립 수집 큐/마이크 검증 아님.")

    def drain(available, flushed):
        nonlocal owned_end, last_start
        while not vad.empty():
            front = vad.front
            start, end = int(front.start), int(front.start) + len(front.samples)
            accepted_frames = ((available + window - 1) // window) * window
            if start < 0 or start < last_start or end <= start or end > accepted_frames:
                raise ValueError("잘못된 VAD 구간")
            # front는 다음 VAD 메서드 호출 전 복사한다.
            vad.pop()
            last_start = start
            clipped_start, clipped_end = min(start, total), min(end, total)
            begin = max(owned_end, clipped_start)
            record = {"raw_start_frame": start, "raw_end_frame": end,
                      "emitted_at_audio_frame": min(available, total), "after_flush": flushed,
                      "emitted_at_wall_s": clock() - started if paced else None,
                      "owned_start_frame": begin, "owned_end_frame": max(begin, clipped_end),
                      "context_start_frame": max(0, begin - round(.2 * rate)),
                      "context_end_frame": max(begin, clipped_end),
                      "overlap_removed_frames": max(0, begin - clipped_start)}
            owned_end = max(owned_end, clipped_end)
            segments.append(record)

    for offset in range(0, total, window):
        block = array("f", samples[offset:offset + window])
        block.extend([0.0] * (window - len(block)))
        due = min(offset + window, total) / rate
        if paced:
            while (remaining := due - (clock() - started)) > 0: sleep(remaining)
            timings["input_lateness_s"].append(max(0.0, clock() - started - due))
        began = clock()
        vad.accept_waveform(convert(block))
        drain(min(offset + window, total), False)
        timings["processing_wall_s"] += clock() - began
    began = clock(); vad.flush(); drain(total, True)
    timings["processing_wall_s"] += clock() - began
    timings["elapsed_wall_s"] = clock() - started
    return segments


def source_metrics(segments, source_start, source_end, *, expected_speech, rate=16000):
    if not 0 <= source_start < source_end: raise ValueError("원본 WAV 범위가 필요합니다.")
    overlap = [s for s in segments if min(s["owned_end_frame"], source_end) > max(s["owned_start_frame"], source_start)]
    last = overlap[-1] if overlap else None
    proxy = ((last["emitted_at_audio_frame"] - source_end) / rate if last and not last["after_flush"] else None)
    wall_proxy = (last["emitted_at_wall_s"] - source_end/rate
                  if last and not last["after_flush"] and last.get("emitted_at_wall_s") is not None else None)
    return {"expected_speech": expected_speech, "detected_segments": len(segments),
            "speech_clip_fully_missed": not overlap if expected_speech else None,
            "silence_or_noise_triggered": bool(segments) if not expected_speech else None,
            "endpoint_from_source_wav_end_audio_s": proxy,
            "endpoint_from_source_wav_end_wall_s": wall_proxy,
            "endpoint_reason": "원본 WAV 끝 대리값; 음소 정렬 아님" if proxy is not None else "검출 없음 또는 파일 종료 flush에서만 확정",
            "acoustic_endpoint_delay_s": None, "paced_wall_endpoint_delay_s": None,
            "source_envelope_covered_frames": sum(min(s["owned_end_frame"], source_end) - max(s["owned_start_frame"], source_start) for s in overlap),
            "word_truncation_or_duplication": None}
