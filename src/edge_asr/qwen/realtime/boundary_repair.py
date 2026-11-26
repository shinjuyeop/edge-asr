"""강제 경계를 넘겨 들은 부분 결과로 직전 마지막 어절만 검증한다."""

import re
from dataclasses import dataclass

from edge_asr.metrics import normalize

CONTEXT_FRAMES = 12 * 512
COMMIT_WAIT_S = 3.0
TERMINAL = re.compile(r"[.!?。？！…]+$")


def tail(text):
    """자연 종결처럼 보이는 마지막 어절의 텍스트 범위와 정규화 형태."""
    match = re.search(r"(?<=\s)\S+\s*$", text)
    if match is None or not TERMINAL.search(text.rstrip()):
        return None
    word = normalize(match.group())
    return (match.start(), word) if len(word) >= 2 and word.isalpha() else None


def repair_tail(left, probe, right):
    """겹친 모델과 소유 PCM의 모델이 각각 같은 첫 두 어절을 낸 때만 확인한다."""
    last = tail(left)
    if last is None or not probe or not right:
        return left
    start, fragment = last
    prefix = [word for raw in right.split()[:2] if (word := normalize(raw))]
    borrowed = [word for raw in probe.split()[:4] if (word := normalize(raw))]
    if (
        len(prefix) != 2
        or len(prefix[1]) < 2
        or prefix[0] == fragment
        or not prefix[0].startswith(fragment)
        or prefix[0] == prefix[1]
    ):
        return left
    # 대여 PCM 앞쪽에 같은 어절이나 별도 본문이 있으면 어느 반복인지 판정할 수 없다.
    if borrowed[:2] != prefix or fragment in borrowed[2:]:
        return left
    return left[:start].rstrip()


@dataclass
class PendingBoundary:
    result: dict
    deadline: float
    offered: bool = False
    probe: str | None = None

    def follows(self, segment_id, begin):
        return segment_id == self.result["segment_id"] + 1 and begin == self.result["end_frame"]
