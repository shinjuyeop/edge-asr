"""Seoul Corpus의 full-text IntervalTier를 읽는다. 범용 Praat 대체물은 아니다."""

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Tier:
    name: str
    intervals: tuple[Interval, ...]


@dataclass(frozen=True)
class TextGrid:
    start: float
    end: float
    tiers: tuple[Tier, ...]


def read_textgrid(blob: bytes) -> TextGrid:
    """중복 tier 이름을 보존하고, 잘린 파일·시간 역전·범위 불일치를 거부한다."""
    text = blob.decode("utf-16" if blob[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
    pos = 0

    def take(pattern):
        nonlocal pos
        match = re.compile(r"\s*" + pattern).match(text, pos)
        if match is None:
            raise ValueError(f"지원하지 않거나 손상된 TextGrid: 문자 {pos}")
        pos = match.end()
        return match.group(1) if match.lastindex else None

    def quoted(key):
        return take(re.escape(key) + r'\s*=\s*"((?:[^\"]|\"\")*)"').replace('""', '"')

    def number(key):
        value = float(take(re.escape(key) + r"\s*=\s*([-+0-9.eE]+)"))
        if not math.isfinite(value):
            raise ValueError("유한한 TextGrid 시각이 필요합니다.")
        return value

    def count(key):
        value = int(take(re.escape(key) + r"\s*=\s*(\d+)"))
        if not 1 <= value <= 100000:
            raise ValueError("지원 범위 밖의 TextGrid 항목 수")
        return value

    def same(a, b):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-7)

    if quoted("File type") != "ooTextFile" or quoted("Object class") != "TextGrid":
        raise ValueError("full-text TextGrid가 필요합니다.")
    start, end = number("xmin"), number("xmax")
    if not 0 <= start < end:
        raise ValueError("잘못된 TextGrid 시간 범위")
    take(r"tiers\?\s*<exists>")
    size = count("size")
    take(r"item\s*\[\]:")
    tiers = []
    for index in range(1, size + 1):
        if int(take(r"item\s*\[(\d+)\]:")) != index:
            raise ValueError("tier 번호 불일치")
        if quoted("class") != "IntervalTier":
            raise ValueError("IntervalTier만 지원합니다.")
        name = quoted("name")
        if not same(number("xmin"), start) or not same(number("xmax"), end):
            raise ValueError("tier와 파일의 시간 범위 불일치")
        intervals = []
        previous = start
        for i in range(1, count("intervals: size") + 1):
            if int(take(r"intervals\s*\[(\d+)\]:")) != i:
                raise ValueError("구간 번호 불일치")
            lo, hi = number("xmin"), number("xmax")
            label = quoted("text")
            if not same(lo, previous) or not start <= lo < hi <= end + 1e-7:
                raise ValueError(f"{name}: 구간 역전·겹침·틈·범위 초과")
            intervals.append(Interval(lo, hi, label))
            previous = hi
        if not same(previous, end):
            raise ValueError("tier의 마지막 구간이 파일 끝과 다릅니다.")
        tiers.append(Tier(name, tuple(intervals)))
    if text[pos:].strip():
        raise ValueError("TextGrid 뒤에 해석되지 않은 내용이 있습니다.")
    return TextGrid(start, end, tuple(tiers))
