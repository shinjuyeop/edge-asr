"""모델 독립 PCM16 전처리 후보. 100ms 블록의 현재/과거 입력만 사용한다."""

import math
from array import array
from collections import deque

POLICIES = {
    "identity": {"kind": "identity"},
    "gain12": {"kind": "fixed_gain", "gain_db": 12.0},
    "rms26": {
        "kind": "causal_rms",
        "target_dbfs": -26.0,
        "max_gain_db": 24.0,
        "floor_dbfs": -60.0,
        "window_blocks": 5,
        "rise_s": 0.3,
        "peak_limit": 0.98,
    },
}
BLOCK_FRAMES = 1600


def rms_dbfs(samples):
    if not samples:
        raise ValueError("비어 있지 않은 PCM 입력이 필요합니다.")
    power = sum(float(x) ** 2 for x in samples) / len(samples)
    return 10 * math.log10(power) if power > 0 else None


class AudioPreprocessor:
    """16kHz/100ms 블록 상태. reset은 명시적 세션 경계에서만 호출한다."""

    def __init__(self, policy):
        if policy not in POLICIES:
            raise ValueError("알려진 정책이 필요합니다.")
        self.policy = policy
        self.settings = dict(POLICIES[policy])
        self.reset()

    def reset(self):
        self.powers = deque(maxlen=5)
        self.previous_gain = 1.0
        self.frames = 0
        self.closed = False

    def process_block(self, block, *, final=False):
        if self.closed:
            raise ValueError("종료한 세션은 reset 후 사용하세요.")
        if not block or len(block) > BLOCK_FRAMES or (len(block) != BLOCK_FRAMES and not final):
            raise ValueError("100ms 블록 또는 final인 마지막 짧은 블록이 필요합니다.")
        if any(not math.isfinite(x) or not -1 <= x <= 1 for x in block):
            raise ValueError("PCM은 -1~1 범위의 유한한 값이어야 합니다.")
        settings = self.settings
        power = sum(float(x) ** 2 for x in block)
        peak = max(abs(x) for x in block)
        self.powers.append((power, len(block)))
        gain = 1.0
        if self.policy == "gain12":
            gain = 10 ** (settings["gain_db"] / 20)
        elif self.policy == "rms26":
            rolling = math.sqrt(sum(p for p, _ in self.powers) / sum(n for _, n in self.powers))
            floor = 10 ** (settings["floor_dbfs"] / 20)
            if rolling > floor:
                desired = max(
                    1.0,
                    min(
                        10 ** (settings["max_gain_db"] / 20),
                        10 ** (settings["target_dbfs"] / 20) / rolling,
                    ),
                )
                alpha = 1 - math.exp(-(len(block) / 16000) / settings["rise_s"])
                gain = (
                    self.previous_gain + alpha * (desired - self.previous_gain)
                    if desired > self.previous_gain
                    else desired
                )
            # 현재 블록을 받은 후 적용하는 peak 제한. 다음 블록을 미리 읽지 않는다.
            if peak:
                gain = min(gain, settings["peak_limit"] / peak)
        self.previous_gain = gain
        output, clipped = array("h"), 0
        for value in block:
            scaled = float(value) * gain
            clipped += scaled > 32767 / 32768 or scaled < -1
            output.append(max(-32768, min(32767, round(scaled * 32768))))
        trace = {"start_frame": self.frames, "end_frame": self.frames + len(block), "gain": gain}
        self.frames += len(block)
        self.closed = final
        return output, {**trace, "newly_clipped_samples": int(clipped)}


def preprocess(samples, policy):
    """M3-0 파일 API를 유지하며 호출마다 상태를 초기화한다."""
    if not samples:
        raise ValueError("비어 있지 않은 PCM 입력이 필요합니다.")
    processor = AudioPreprocessor(policy)
    output, trace, clipped = array("h"), [], 0
    for start in range(0, len(samples), BLOCK_FRAMES):
        pcm, info = processor.process_block(
            samples[start : start + BLOCK_FRAMES], final=start + BLOCK_FRAMES >= len(samples)
        )
        output.extend(pcm)
        clipped += info.pop("newly_clipped_samples")
        trace.append(info)
    floats = array("f", (x / 32768 for x in output))
    return output, {
        "policy": policy,
        "settings": processor.settings,
        "frames": len(output),
        "input_rms_dbfs": rms_dbfs(samples),
        "output_rms_dbfs": rms_dbfs(floats),
        "newly_clipped_samples": int(clipped),
        "clipped_fraction": clipped / len(output),
        "output_peak": max(abs(x) for x in floats),
        "gain_trace": trace,
        "causality": "100ms 입력 블록이 도착한 후 처리. 블록 경계의 미래 입력은 참조하지 않음.",
        "state_scope": "발화마다 초기화; 연속 스트림 경계와 gain 변화 청감은 미검증",
    }
