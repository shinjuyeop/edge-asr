"""고정 Zeroth test 원본의 출처·archive 읽기·메타데이터 후보. 선정 정책은 호출자가 정한다."""

import tarfile
from pathlib import PurePosixPath

from edge_asr.tools.data.external import checked_member

SLR = "https://www.openslr.org/resources/40/"
ETAG = '"2684b9daa-567cdc82e7e00"'
PREFIX_SOURCE = (
    SLR + "zeroth_korean.tar.gz",
    "7cafae27a70315876bfea15406562e80a6f922d1fc437716a658020e64d97d41",
    134217728,
)


def members(cache):
    """전체 gzip의 앞부분에서 완전한 test만 읽는다. 전체 archive MD5 검증과 구분한다."""
    files = {}
    with tarfile.open(cache / "zeroth-prefix.bin", mode="r|gz") as archive:
        for member in archive:
            # 다음 train 디렉터리의 시작 헤더까지 읽어 test 수집의 완료를 확인한다.
            if member.name == "train_data_01" and member.isdir():
                break
            path = checked_member(member)
            if str(path) != "AUDIO_INFO" and path.parts[0] != "test_data_01":
                raise ValueError("예상하지 않은 Zeroth test 원본 경로")
            if member.isfile():
                if member.name in files:
                    raise ValueError("중복 tar member")
                files[member.name] = archive.extractfile(member).read()
        else:
            raise ValueError(
                "test 끝/train 시작 헤더가 없어 완전한 test 수집을 확인할 수 없습니다."
            )
    if sum(name.endswith(".flac") for name in files) != 457:
        raise ValueError("공식 Zeroth test457개가 필요합니다.")
    return files


def flac_info(blob):
    # FLAC STREAMINFO 첫 블록: sample rate20, channels3, bits5, total samples36 bits.
    if (
        len(blob) < 42
        or blob[:4] != b"fLaC"
        or blob[4] & 0x7F
        or int.from_bytes(blob[5:8], "big") != 34
    ):
        raise ValueError("FLAC STREAMINFO가 필요합니다.")
    packed = int.from_bytes(blob[18:26], "big")
    rate, channels, bits, frames = (
        packed >> 44,
        ((packed >> 41) & 7) + 1,
        ((packed >> 36) & 31) + 1,
        packed & ((1 << 36) - 1),
    )
    if (rate, channels, bits) != (16000, 1, 16) or not 0 < frames < 30 * rate:
        raise ValueError("30초 미만의 16kHz mono PCM16 원음이 필요합니다.")
    return frames


def candidates(files):
    speakers = {}
    for line in files["AUDIO_INFO"].decode("utf-8").splitlines()[1:]:
        speaker, _, sex, script, split = line.strip().split("|")
        if split == "test_data_01":
            speakers[speaker] = {"gender": {"m": "male", "f": "female"}[sex], "script": script}
    if len(speakers) != 10:
        raise ValueError("Zeroth test 화자10명이 필요합니다.")
    references = {}
    for name, blob in files.items():
        if name.endswith(".trans.txt"):
            for line in blob.decode("utf-8").splitlines():
                key, text = line.split(maxsplit=1)
                if key in references:
                    raise ValueError("중복 Zeroth 전사 ID")
                references[key] = text
    rows = []
    for name, blob in files.items():
        if not name.endswith(".flac"):
            continue
        path = PurePosixPath(name)
        speaker = path.parent.name
        if speaker not in speakers or path.parts[1] != speakers[speaker]["script"]:
            raise ValueError("Zeroth 음원과 화자/스크립트 정보 불일치")
        rows.append(
            {
                "id": "zeroth-" + path.stem,
                "source": "zeroth",
                "member": name,
                "reference": references[path.stem],
                "frames": flac_info(blob),
                "speaker_id": speaker,
                "gender": speakers[speaker]["gender"],
                "source_split": "test_data_01",
            }
        )
    if len(rows) != len(references):
        raise ValueError("Zeroth 음원과 전사 개수 불일치")
    return rows
