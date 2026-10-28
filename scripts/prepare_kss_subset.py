"""Host에서 KSS의 고정 50개 발화와 전사를 받아 16kHz 개발용 목록을 준비한다."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

DATASET = "Bingsu/KSS_Dataset"
REVISION = "48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c"
OFFSET = 1000
COUNT = 50
OUTPUT = Path("data/evaluation/kss-dev50")
RECIPE = {"name": "kss-dev50-v1", "dataset": DATASET, "revision": REVISION,
          "source_config": "default", "source_split": "train", "offset": OFFSET,
          "count": COUNT, "reference_field": "expanded_script",
          "sample_rate_hz": 16000, "channels": 1, "sample_width_bytes": 2,
          "license": "cc-by-nc-sa-4.0"}


def fetch(url: str, limit: int) -> tuple[bytes, object]:
    request = urllib.request.Request(url, headers={"User-Agent": "edge-asr-dataset-preparation/0.1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                blob = response.read(limit + 1)
                if len(blob) > limit:
                    raise ValueError("다운로드 크기 제한을 초과했습니다.")
                return blob, response.headers
        except urllib.error.HTTPError as error:
            if attempt == 2 or error.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def wav_info(path: Path, sample_rate: int) -> dict:
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, sample_rate, "NONE"):
            raise ValueError(f"mono PCM16 {sample_rate}Hz WAV가 아닙니다: {path}")
        frames = wav.getnframes()
        if not 0 < frames <= sample_rate * 30 or len(wav.readframes(frames)) != frames * 2:
            raise ValueError(f"비어 있거나 잘렸거나 30초를 초과하는 WAV입니다: {path}")
    return {"frames": frames, "duration_s": frames / sample_rate, "sample_rate_hz": sample_rate}


def convert(source: Path, destination: Path, ffmpeg: str) -> dict:
    original = wav_info(source, 44100)
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                    "-i", str(source), "-map_metadata", "-1", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                    str(destination)], check=True, capture_output=True, timeout=30)
    converted = wav_info(destination, 16000)
    if abs(original["duration_s"] - converted["duration_s"]) > 0.001:
        raise ValueError("변환 후 오디오 길이가 원본과 다릅니다.")
    return converted


def validate_rows(payload: dict, revision: str | None) -> list[dict]:
    if revision != REVISION:
        raise ValueError(f"Dataset Viewer 리비전이 고정값과 다릅니다: {revision}. 자동 갱신하지 않습니다.")
    if not isinstance(payload, dict):
        raise ValueError("Dataset Viewer 객체 응답이 필요합니다.")
    rows = payload.get("rows")
    if payload.get("partial") is not False or not isinstance(rows, list) or len(rows) != COUNT:
        raise ValueError("정확한 50개 전체 행 응답이 필요합니다.")
    for index, item in enumerate(rows, OFFSET):
        if not isinstance(item, dict) or not isinstance(item.get("row"), dict):
            raise ValueError("잘못된 데이터 행입니다.")
        row = item.get("row", {})
        if item.get("row_idx") != index or item.get("truncated_cells"):
            raise ValueError("원본 행 번호·순서 또는 전사가 잘못됐습니다.")
        if not isinstance(row.get("expanded_script"), str) or not row["expanded_script"].strip():
            raise ValueError("배포처 expanded_script 전사가 필요합니다.")
        audio = row.get("audio")
        if not isinstance(audio, list) or len(audio) != 1 or not isinstance(audio[0], dict) or not isinstance(audio[0].get("src"), str):
            raise ValueError("한 행에 오디오 URL 하나가 필요합니다.")
        url = urllib.parse.urlsplit(audio[0]["src"])
        expected = f"/cached-assets/{DATASET}/--/{REVISION}/--/default/train/{index}/audio/audio.wav"
        if url.scheme != "https" or url.netloc != "datasets-server.huggingface.co" or url.path != expected:
            raise ValueError("오디오 URL이 고정 데이터셋·리비전·행에 속하지 않습니다.")
    return rows


def reuse(destination: Path) -> Path:
    provenance = json.loads((destination / "provenance.json").read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or provenance.get("recipe") != RECIPE:
        raise ValueError("기존 데이터셋의 준비 조건이 다릅니다. 덮어쓰지 않습니다.")
    expected = {"manifest.jsonl", "source_rows.json", "SOURCE_README.md"}
    expected.update(f"{folder}/{index:05d}.wav" for folder in ("original", "wav")
                    for index in range(OFFSET, OFFSET + COUNT))
    files = provenance.get("files", {})
    if not isinstance(files, dict) or set(files) != expected:
        raise ValueError("기존 데이터셋의 파일 목록이 불완전합니다.")
    for name, checksum in files.items():
        path = destination / name
        if path.is_symlink() or not path.is_file() or sha256(path) != checksum:
            raise ValueError(f"기존 데이터셋 파일이 변경됐습니다. 덮어쓰지 않습니다: {name}")
    return destination / "manifest.jsonl"


def prepare(root: Path, destination: Path, ffmpeg: str = "ffmpeg") -> Path:
    root, destination = root.resolve(), destination.resolve()
    relative = destination.relative_to(root)
    if not relative.parts or relative.parts[0] != "data":
        raise ValueError("준비 경로는 프로젝트의 Git 제외 data/ 안이어야 합니다.")
    if destination.exists():
        return reuse(destination)
    executable = shutil.which(ffmpeg)
    if executable is None:
        raise ValueError("Host에 ffmpeg가 필요합니다. RP5 추론 환경에는 추가 설치하지 않습니다.")
    version = subprocess.check_output([executable, "-version"], text=True, timeout=10).splitlines()[0]
    query = urllib.parse.urlencode({"dataset": DATASET, "config": "default", "split": "train",
                                   "offset": OFFSET, "length": COUNT})
    rows_url = "https://datasets-server.huggingface.co/rows?" + query
    blob, headers = fetch(rows_url, 2 * 1024 * 1024)
    rows = validate_rows(json.loads(blob), headers.get("x-revision"))
    card_url = f"https://huggingface.co/datasets/{DATASET}/raw/{REVISION}/README.md"
    card, _ = fetch(card_url, 128 * 1024)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".kss-dev50-", dir=destination.parent))
    try:
        (stage / "original").mkdir()
        (stage / "wav").mkdir()
        (stage / "source_rows.json").write_bytes(blob)
        (stage / "SOURCE_README.md").write_bytes(card)
        manifest, details = [], []
        for number, item in enumerate(rows, 1):
            index, row = item["row_idx"], item["row"]
            name = f"{index:05d}.wav"
            original, converted = stage / "original" / name, stage / "wav" / name
            audio_url = row["audio"][0]["src"]
            audio_blob, _ = fetch(audio_url, 4 * 1024 * 1024)
            original.write_bytes(audio_blob)
            info = convert(original, converted, executable)
            manifest.append({"id": f"kss-train-{index:05d}",
                             "audio_path": (relative / "wav" / name).as_posix(),
                             "audio_sha256": sha256(converted), "reference": row["expanded_script"],
                             "split": "development", "condition": "kss_read_speech",
                             "dataset": DATASET, "dataset_revision": REVISION,
                             "source_split": "train", "source_row": index,
                             "reference_field": "expanded_script", "license": RECIPE["license"]})
            details.append({"row": index, **info,
                            "original_sha256": sha256(original), "converted_sha256": sha256(converted),
                            "audio_source": audio_url.split("?", 1)[0]})
            print(f"KSS 준비 {number}/{COUNT}: 행 {index}, {info['duration_s']:.3f}초", flush=True)
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in manifest)
        (stage / "manifest.jsonl").write_text(text, encoding="utf-8")
        provenance = {"schema_version": 1, "recipe": RECIPE,
                      "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
                      "rows_url": rows_url, "rows_revision_header": headers.get("x-revision"),
                      "dataset_card": card_url, "ffmpeg": version,
                      "conversion": "44100Hz mono PCM16 → ffmpeg → 16000Hz mono PCM16; metadata removed, bitexact flags",
                      "sampling": "train 행 1000~1049 연속 선택; ASR 결과를 보기 전에 고정",
                      "limitations": "단일 여성 화자의 낭독; 개발용 50개 발화 각 1회. 독립 test split·다화자·잡음 평가 아님. 모델 학습 데이터와의 중복 미확인.",
                      "audio": details,
                      "files": {p.relative_to(stage).as_posix(): sha256(p)
                                for p in sorted(stage.rglob("*")) if p.is_file()}}
        (stage / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise ValueError("준비 중 대상 경로가 생겼습니다. 덮어쓰지 않습니다.")
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination / "manifest.jsonl"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Host에서 고정 KSS 개발용 50개 발화 준비 (CC BY-NC-SA 4.0)")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    destination = args.output_dir.expanduser()
    destination = destination if destination.is_absolute() else root / destination
    try:
        manifest = prepare(root, destination, args.ffmpeg)
    except (OSError, ValueError, subprocess.SubprocessError, wave.Error) as error:
        parser.exit(2, f"오류: {error}\n")
    print(f"KSS 개발용 목록 준비 완료: {manifest}")
    print("단일 화자 50개 발화입니다. 음원·전사·출처·변환 이력은 data/에 보관하고 Git에서 제외합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
