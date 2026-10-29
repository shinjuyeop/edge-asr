"""모델 독립 PCM16 전처리 후보. 100ms 블록의 현재/과거 입력만 사용한다."""

from array import array
from collections import deque
import math

POLICIES = {
    "identity": {"kind": "identity"},
    "gain12": {"kind": "fixed_gain", "gain_db": 12.0},
    "rms26": {"kind": "causal_rms", "target_dbfs": -26.0, "max_gain_db": 24.0,
              "floor_dbfs": -60.0, "window_blocks": 5, "rise_s": 0.3, "peak_limit": 0.98},
}
BLOCK_FRAMES = 1600


def rms_dbfs(samples):
    if not samples:
        raise ValueError("비어 있지 않은 PCM 입력이 필요합니다.")
    power = sum(float(x) ** 2 for x in samples) / len(samples)
    return 10 * math.log10(power) if power > 0 else None


def preprocess(samples, policy):
    """정책마다 발화 시작에서 상태를 초기화한다. 실서비스 연속 상태와는 별도 검증이다."""
    if policy not in POLICIES or not samples:
        raise ValueError("알려진 정책과 비어 있지 않은 입력이 필요합니다.")
    if any(not math.isfinite(x) or not -1 <= x <= 1 for x in samples):
        raise ValueError("PCM은 -1~1 범위의 유한한 값이어야 합니다.")
    settings = POLICIES[policy]
    output, trace, powers = array("h"), [], deque(maxlen=5)
    previous_gain, clipped = 1.0, 0
    for start in range(0, len(samples), BLOCK_FRAMES):
        block = samples[start:start + BLOCK_FRAMES]
        power = sum(float(x) ** 2 for x in block)
        peak = max(abs(x) for x in block)
        powers.append((power, len(block)))
        gain = 1.0
        if policy == "gain12":
            gain = 10 ** (settings["gain_db"] / 20)
        elif policy == "rms26":
            rolling = math.sqrt(sum(p for p, _ in powers) / sum(n for _, n in powers))
            floor = 10 ** (settings["floor_dbfs"] / 20)
            if rolling > floor:
                desired = max(1.0, min(10 ** (settings["max_gain_db"] / 20),
                                       10 ** (settings["target_dbfs"] / 20) / rolling))
                alpha = 1 - math.exp(-(len(block) / 16000) / settings["rise_s"])
                gain = previous_gain + alpha * (desired - previous_gain) if desired > previous_gain else desired
            # 현재 블록을 받은 후 적용하는 peak 제한. 다음 블록을 미리 읽지 않는다.
            if peak:
                gain = min(gain, settings["peak_limit"] / peak)
        previous_gain = gain
        for value in block:
            scaled = float(value) * gain
            clipped += scaled > 32767 / 32768 or scaled < -1
            output.append(max(-32768, min(32767, round(scaled * 32768))))
        trace.append({"start_frame": start, "end_frame": start + len(block), "gain": gain})
    floats = array("f", (x / 32768 for x in output))
    return output, {"policy": policy, "settings": settings, "frames": len(output),
                    "input_rms_dbfs": rms_dbfs(samples), "output_rms_dbfs": rms_dbfs(floats),
                    "newly_clipped_samples": int(clipped), "clipped_fraction": clipped / len(output),
                    "output_peak": max(abs(x) for x in floats), "gain_trace": trace,
                    "causality": "100ms 입력 블록이 도착한 후 처리. 블록 경계의 미래 입력은 참조하지 않음.",
                    "state_scope": "발화마다 초기화; 연속 스트림 경계와 gain 변화 청감은 미검증"}
