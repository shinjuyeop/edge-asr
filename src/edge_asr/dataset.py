"""모델과 독립적인 JSONL 평가 목록 및 PCM16 WAV 입력."""

import hashlib
import io
import json
import re
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    id: str
    audio_path: Path
    reference: str
    split: str = "evaluation"
    condition: str = "unspecified"
    audio_sha256: str | None = None


@dataclass
class Audio:
    samples: array
    sample_rate_hz: int
    sha256: str

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate_hz

    def metadata(self) -> dict:
        return {
            "sha256": self.sha256,
            "frames": len(self.samples),
            "sample_rate_hz": self.sample_rate_hz,
            "channels": 1,
            "sample_width_bytes": 2,
            "duration_s": self.duration_s,
        }


def resolve_path(path: str | Path, root: Path) -> Path:
    value = Path(path).expanduser()
    return (value if value.is_absolute() else root / value).resolve()


def load_manifest(path: Path, root: Path) -> tuple[list[Sample], str]:
    """오디오는 아직 열지 않는다. 잘못된 행·중복 ID는 평가 시작 전에 거부한다."""
    blob = path.read_bytes()
    samples, ids = [], set()
    for number, line in enumerate(blob.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("객체가 필요합니다.")
            for key in ("id", "audio_path", "reference"):
                if not isinstance(row.get(key), str):
                    raise ValueError(f"{key} 문자열이 필요합니다.")
                row[key].encode("utf-8")
            if not row["id"].strip() or not row["audio_path"].strip():
                raise ValueError("id와 audio_path는 비어 있을 수 없습니다.")
            if row["id"] in ids:
                raise ValueError(f"중복 id: {row['id']}")
            for key in ("split", "condition"):
                if key in row and (not isinstance(row[key], str) or not row[key].strip()):
                    raise ValueError(f"{key}는 비어 있지 않은 문자열이어야 합니다.")
            expected_hash = row.get("audio_sha256")
            if expected_hash is not None and (
                not isinstance(expected_hash, str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
            ):
                raise ValueError("audio_sha256는 64자리 SHA-256 문자열이어야 합니다.")
            ids.add(row["id"])
            samples.append(
                Sample(
                    row["id"],
                    resolve_path(row["audio_path"], root),
                    row["reference"],
                    row.get("split", "evaluation"),
                    row.get("condition", "unspecified"),
                    expected_hash.lower() if expected_hash else None,
                )
            )
        except (ValueError, TypeError) as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    if not samples:
        raise ValueError("평가 목록이 비어 있습니다.")
    return samples, hashlib.sha256(blob).hexdigest()


def load_audio(path: Path, sample_rate_hz: int) -> Audio:
    """형식 변환·resampling 없이 검증하고 float PCM으로 준비한다."""
    blob = path.read_bytes()
    with wave.open(io.BytesIO(blob), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (1, 2, "NONE"):
            raise ValueError("mono PCM16 WAV가 필요합니다.")
        if wav.getframerate() != sample_rate_hz:
            raise ValueError(f"샘플레이트 불일치: {wav.getframerate()} != {sample_rate_hz}")
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
    if frames == 0 or len(pcm) != frames * 2:
        raise ValueError("WAV가 비어 있거나 프레임 데이터가 잘렸습니다.")
    integers = array("h")
    integers.frombytes(pcm)
    if sys.byteorder != "little":
        integers.byteswap()
    samples = array("f", (value / 32768.0 for value in integers))
    return Audio(samples, sample_rate_hz, hashlib.sha256(blob).hexdigest())


def pcm_bytes(pcm):
    value = array("h", pcm)
    if sys.byteorder != "little":
        value.byteswap()
    return value.tobytes()


def write_wav(path, pcm):
    """mono PCM16/16kHz를 쓴다. bytes는 little-endian PCM, 정수 배열은 명시적으로 변환한다."""
    data = pcm if isinstance(pcm, bytes) else pcm_bytes(pcm)
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(data)
