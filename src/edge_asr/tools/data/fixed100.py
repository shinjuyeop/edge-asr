"""공식 원본에서 고정100개를 준비한다. ASR 추론·기존 자료 수정은 하지 않는다."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
import wave
from collections import Counter
from pathlib import Path, PurePosixPath

from edge_asr.artifacts import digest, write_json, write_jsonl
from edge_asr.dataset import load_audio, load_manifest
from edge_asr.metrics import normalize
from edge_asr.tools.data.external import finish, verify_existing

FLEURS_REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
HF = f"https://huggingface.co/datasets/google/fleurs/resolve/{FLEURS_REVISION}/"
SLR = "https://www.openslr.org/resources/40/"
ETAG = '"2684b9daa-567cdc82e7e00"'
SOURCES = {
    "zeroth-prefix.bin": (
        SLR + "zeroth_korean.tar.gz",
        "7cafae27a70315876bfea15406562e80a6f922d1fc437716a658020e64d97d41",
        134217728,
    ),
    "fleurs-test.tar.gz": (
        HF + "data/ko_kr/audio/test.tar.gz",
        "3489e529f2aad18d3357b746c5f955941d258b79dc60d8a102d5bced2a223184",
        214425558,
    ),
    "fleurs-test.tsv": (
        HF + "data/ko_kr/test.tsv",
        "cf2f7c8765f6203e3c46ef620d5e936d3f331b6e8865455557777dd1347517f5",
        211844,
    ),
    "FLEURS_README.md": (
        HF + "README.md",
        "688f79f2a5c731af3796e9f683eb02f9b3f09d040decd8c5625d0f37098e71c6",
        None,
    ),
    "ZEROTH_info.txt": (
        SLR + "info.txt",
        "a6a9252ee4c93c0c18cf446ba41f82e260aaaf40447da0a3adffac71af3c16e2",
        None,
    ),
    "ZEROTH_about.html": (
        SLR + "about.html",
        "f367f9c7b73c70c5c5f4e5115d28133532f002857978098c7f9ad6f2864c5f92",
        None,
    ),
    "ZEROTH_checksum.md5": (
        SLR + "checksum.md5",
        "8945b198864a404b8948d11ba5c5467597c991e56819acdd4bf9871de160faba",
        None,
    ),
}
EXCLUDE = (
    "data/evaluation/kss-dev50/manifest.jsonl",
    "data/evaluation/fleurs-ko-test100/manifest.jsonl",
    "data/evaluation/fleurs-prospective30-20261106-01/manifest.jsonl",
)
RECIPE = {
    "name": "qwen-fixed100-v1",
    "seed": "edge-asr-fixed100-20261109-v1",
    "count": 100,
    "source_counts": {"zeroth": 60, "fleurs": 40},
    "zeroth_per_speaker": {"0-10s": 5, "10-30s": 1},
    "fleurs_gender_duration_quotas": {"male": [20, 4, 1], "female": [11, 3, 1]},
    "fleurs_duration_boundaries_s": [0, 10, 15, 30],
    "order": "source_id_lexical",
    "normalization": "ko_cer_v1",
    "audio": "16000Hz mono PCM16, no resampling, trimming, gain, padding",
    "role": "development_regression_not_independent_holdout",
    "license": "cc-by-4.0",
    "target_duration_s": [600, 900],
}


def check_sources(cache):
    for name, (_, expected, size) in SOURCES.items():
        path = cache / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError(f"고정한 공식 원본 SHA-256 불일치/누락: {name}")
        if size is not None and path.stat().st_size != size:
            raise ValueError(f"원본 크기 불일치: {name}")


def download_sources(cache):
    cache.mkdir(parents=True, exist_ok=True)
    for name, (url, expected, size) in SOURCES.items():
        path = cache / name
        if path.exists():
            if path.is_symlink() or digest(path) != expected:
                raise ValueError(f"기존 원본을 덮어쓰지 않습니다: {path}")
            continue
        headers = {"User-Agent": "edge-asr-fixed100/1"}
        if name == "zeroth-prefix.bin":
            headers.update(Range=f"bytes=0-{size - 1}", **{"If-Match": ETAG})
        temporary = path.with_suffix(path.suffix + ".part")
        with (
            urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=30
            ) as response,
            temporary.open("xb") as out,
        ):
            if name == "zeroth-prefix.bin" and (
                response.status != 206
                or response.headers.get("ETag") != ETAG
                or response.headers.get("Content-Range") != "bytes 0-134217727/10339720618"
            ):
                raise ValueError("공식 Zeroth 부분 응답의 ETag/Range 불일치")
            count = 0
            while block := response.read(1024**2):
                count += len(block)
                if count > (size or 2 * 1024**2):
                    raise ValueError("원본 다운로드 크기 상한 초과")
                out.write(block)
        if digest(temporary) != expected or (size is not None and count != size):
            raise ValueError(f"다운로드가 고정한 원본과 다릅니다: {name}")
        temporary.rename(path)
        print(f"downloaded: {name}", flush=True)
    check_sources(cache)


def checked_member(member):
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
        raise ValueError("안전하지 않은 원본 tar 경로")
    if not member.isdir() and not member.isfile():
        raise ValueError("원본 tar의 일반 파일/디렉터리만 허용합니다.")
    if member.size > 16 * 1024**2:
        raise ValueError("개별 원음/메타데이터 크기 상한 초과")
    return path


def zeroth_members(cache):
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


def zeroth_candidates(files):
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


def exclusions(root):
    texts, sentence_ids, hashes = set(), set(), {}
    for name in EXCLUDE:
        path = root / name
        hashes[name] = digest(path)
        for line in path.read_text().splitlines():
            row = json.loads(line)
            texts.add(normalize(row["reference"]))
            if row.get("dataset") == "google/fleurs":
                sentence_ids.add(row["sentence_id"])
    return texts, sentence_ids, hashes


def fleurs_candidates(cache, sentence_ids):
    rows = []
    for i, line in enumerate((cache / "fleurs-test.tsv").read_text().splitlines()):
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError("FLEURS 공식 TSV 7개 필드가 필요합니다.")
        sid, name, _, reference, _, frames, gender = fields
        frames = int(frames)
        if PurePosixPath(name).name != name or not name.endswith(".wav"):
            raise ValueError("FLEURS 원본 파일명 형식 오류")
        if int(sid) in sentence_ids:
            continue
        if not 0 < frames < 30 * 16000:
            raise ValueError("FLEURS 길이 형식 오류")
        rows.append(
            {
                "id": "fleurs-" + Path(name).stem,
                "source": "fleurs",
                "member": name,
                "reference": reference,
                "frames": frames,
                "speaker_id": None,
                "gender": {"MALE": "male", "FEMALE": "female"}[gender],
                "sentence_id": int(sid),
                "source_tsv_row": i,
                "source_split": "test",
            }
        )
    return rows


def select(candidates, excluded):
    seen = set(excluded)
    selected = []
    key = lambda r: hashlib.sha256((RECIPE["seed"] + "/" + r["id"]).encode()).hexdigest()

    def take(pool, count):
        picked = []
        for row in sorted(pool, key=key):
            text = normalize(row["reference"])
            if not text or text in seen:
                continue
            seen.add(text)
            picked.append(row)
            if len(picked) == count:
                break
        if len(picked) != count:
            raise ValueError("선정 조건·중복 제외 후 고정 할당량이 부족합니다.")
        selected.extend(picked)

    zeroth = [r for r in candidates if r["source"] == "zeroth"]
    for speaker in sorted({r["speaker_id"] for r in zeroth}):
        group = [r for r in zeroth if r["speaker_id"] == speaker]
        take([r for r in group if r["frames"] < 10 * 16000], 5)
        take([r for r in group if r["frames"] >= 10 * 16000], 1)
    for gender, counts in RECIPE["fleurs_gender_duration_quotas"].items():
        for (low, high), count in zip(((0, 10), (10, 15), (15, 30)), counts):
            take(
                [
                    r
                    for r in candidates
                    if r["source"] == "fleurs"
                    and r["gender"] == gender
                    and low * 16000 <= r["frames"] < high * 16000
                ],
                count,
            )
    if len(selected) != 100 or Counter(r["source"] for r in selected) != RECIPE["source_counts"]:
        raise ValueError("고정100개·자료별 개수 불일치")
    total = sum(r["frames"] for r in selected) / 16000
    if not 600 <= total <= 900:
        raise ValueError(f"총 길이 목표10~15분 미충족: {total}초. 목록을 자동 변경하지 않습니다.")
    return sorted(selected, key=lambda r: r["id"])


def convert(original, output, frames):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-n",
            "-i",
            str(original),
            "-map_metadata",
            "-1",
            "-c:a",
            "pcm_s16le",
            "-fflags",
            "+bitexact",
            "-flags:a",
            "+bitexact",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    audio = load_audio(output, 16000)
    if len(audio.samples) != frames:
        raise ValueError("원본 metadata와 PCM 프레임 수 불일치")
    return audio


def verify(root, output):
    info = verify_existing(output, RECIPE)
    rows = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
    samples, _ = load_manifest(output / "manifest.jsonl", root)
    if len(rows) != 100 or len({normalize(r["reference"]) for r in rows}) != 100:
        raise ValueError("100개 고유 입력·정답이 필요합니다.")
    pcm_hashes = set()
    for sample, row in zip(samples, rows):
        audio = load_audio(sample.audio_path, 16000)
        if audio.sha256 != sample.audio_sha256 or len(audio.samples) != row["frames"]:
            raise ValueError("평가 WAV 해시/프레임 수 불일치")
        with wave.open(str(sample.audio_path)) as wav:
            pcm = hashlib.sha256(wav.readframes(wav.getnframes())).hexdigest()
        if pcm != row["pcm16_sha256"] or pcm in pcm_hashes:
            raise ValueError("PCM 해시 불일치/중복")
        pcm_hashes.add(pcm)
    return info


def prepare(root, cache, output):
    if output.exists():
        return verify(root, output)
    output.relative_to(root / "data/evaluation")
    check_sources(cache)
    old_texts, old_ids, excluded_hashes = exclusions(root)
    files = zeroth_members(cache)
    candidates = zeroth_candidates(files) + fleurs_candidates(cache, old_ids)
    selected = select(candidates, old_texts)
    stage = output.with_name("." + output.name + ".preparing")
    stage.mkdir(parents=True, exist_ok=False)
    (stage / "original").mkdir()
    (stage / "wav").mkdir()
    for name in SOURCES:
        if name.endswith((".md", ".txt", ".html", ".md5", ".tsv")):
            shutil.copyfile(cache / name, stage / name)
    for name, blob in files.items():
        if name == "AUDIO_INFO" or name.endswith(".trans.txt"):
            metadata = stage / "metadata" / "zeroth" / name
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_bytes(blob)
    fleurs = {r["member"]: r for r in selected if r["source"] == "fleurs"}
    with tarfile.open(cache / "fleurs-test.tar.gz", "r:gz") as archive:
        for member in archive:
            path = checked_member(member)
            if member.isfile() and path.name in fleurs:
                row = fleurs.pop(path.name)
                blob = archive.extractfile(member).read()
                (stage / "original" / (row["id"] + ".wav")).write_bytes(blob)
    if fleurs:
        raise ValueError("FLEURS 원음이 archive에 없습니다.")
    manifest = []
    for row in selected:
        suffix = ".flac" if row["source"] == "zeroth" else ".wav"
        original = stage / "original" / (row["id"] + suffix)
        if row["source"] == "zeroth":
            original.write_bytes(files[row["member"]])
        converted = stage / "wav" / (row["id"] + ".wav")
        audio = convert(original, converted, row["frames"])
        with wave.open(str(converted)) as wav:
            pcm_hash = hashlib.sha256(wav.readframes(wav.getnframes())).hexdigest()
        manifest.append(
            {
                **row,
                "audio_path": str((output / "wav" / converted.name).relative_to(root)),
                "audio_sha256": audio.sha256,
                "pcm16_sha256": pcm_hash,
                "original_sha256": digest(original),
                "split": "development_regression",
                "condition": row["source"] + "_read_speech",
                "dataset": "Zeroth-Korean/OpenSLR40"
                if row["source"] == "zeroth"
                else "google/fleurs",
                "dataset_revision": "OpenSLR-20180320-prefix-sha256"
                if row["source"] == "zeroth"
                else FLEURS_REVISION,
                "reference_field": "official_trans.txt"
                if row["source"] == "zeroth"
                else "transcription_TSV_column3_zero_based",
                "license": "cc-by-4.0",
            }
        )
    if len({r["pcm16_sha256"] for r in manifest}) != 100:
        raise ValueError("선정100개 사이 중복 PCM")
    write_jsonl(stage / "manifest.jsonl", manifest)
    write_json(
        stage / "selection.json",
        {
            "recipe": RECIPE,
            "excluded_manifest_sha256": excluded_hashes,
            "selected": selected,
            "candidate_count": len(candidates),
            "selection_scope": "모델 출력 미사용. 이전 정규화 정답·FLEURS 문장ID 제외. 길이별 할당은 빠른 회귀 검사 목적이며 대표 무작위 표본 아님.",
        },
    )
    stats = {
        "count": 100,
        "total_audio_s": sum(r["frames"] for r in manifest) / 16000,
        "min_audio_s": min(r["frames"] for r in manifest) / 16000,
        "max_audio_s": max(r["frames"] for r in manifest) / 16000,
        "source_counts": dict(Counter(r["source"] for r in manifest)),
        "length_counts": dict(
            Counter(
                "under10s"
                if r["frames"] < 10 * 16000
                else "10to15s"
                if r["frames"] < 15 * 16000
                else "15to30s"
                for r in manifest
            )
        ),
        "gender_counts": dict(Counter(r["gender"] for r in manifest)),
        "zeroth_speaker_counts": dict(
            Counter(r["speaker_id"] for r in manifest if r["source"] == "zeroth")
        ),
        "fleurs_speaker_count": None,
        "reference_characters": sum(len(normalize(r["reference"])) for r in manifest),
        "unmeasured": {"cer": None, "rtf": None, "ttft_s": None},
        "limitations": "낭독·개발 회귀100개. 자유발화·잡음·마이크·30초 이상 입력·독립 holdout·모델 학습 중복 미검증.",
    }
    write_json(stage / "statistics.json", stats)
    receipt = {
        "recipe": RECIPE,
        "sources": {
            name: {"url": url, "sha256": sha, "bytes": (cache / name).stat().st_size}
            for name, (url, sha, _) in SOURCES.items()
        },
        "zeroth_archive_scope": "공식 ETag에 묶은128MiB prefix에서 완전한test457개와 다음train시작 확인. 전체10GB archive MD5는 미검증. prefix SHA와 개별원음 SHA를 고정.",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=root
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root).strip()
        ),
        "preparation_script_sha256": digest(Path(__file__)),
        "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
        "statistics": stats,
    }
    result = finish(stage, output, receipt)
    verify(root, output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("data/sources/edge100-20261109-01")
    )
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/edge100-20261109-01"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    cache, output = args.source_cache.resolve(), args.output.resolve()
    cache.relative_to(root / "data")
    if args.download and args.verify_only:
        parser.error("--download와 --verify-only를 함께 사용할 수 없습니다.")
    if args.download:
        download_sources(cache)
    result = verify(root, output) if args.verify_only else prepare(root, cache, output)
    print(json.dumps(result["statistics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
