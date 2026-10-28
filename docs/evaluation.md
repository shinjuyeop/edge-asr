# 공통 파일 평가 도구

Zipformer를 한 번 로드하고 여러 WAV를 순차 처리하여 CER·batch RTF·추론 구간 CPU/RSS를 기록합니다. 입력·채점·실행·저장은 공통이며 모델 호출만 `adapters/zipformer.py`로 분리했습니다. 저장된 가설은 모델·오디오 없이 재채점할 수 있습니다.

현재 지원은 **CPU Zipformer transducer의 batch 평가**입니다. 온라인 모델에 전체 PCM을 대기 없이 전달하고 파일마다 새 스트림을 생성·flush합니다. 공통 스트리밍 평가·다른 모델·실제 마이크는 아직 지원하지 않습니다. Host의 모의 런타임으로 검사했으며 이 새 도구의 실제 RP5 실행은 미검증입니다.

## RP5 시작

기존 모델·WAV·가상환경을 사용하는 한 프로젝트 폴더에서 실행하며 패키지 재설치는 필요하지 않습니다.

```bash
cd /home/deepet/projects/edge-asr &&
git pull --ff-only origin main &&
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/zipformer_eval.toml
```

`scripts/edge_asr.py`는 현재 `src`를 사용합니다. 설치한 환경에서는 `edge-asr evaluate`, `edge-asr score`도 같은 기능을 제공합니다.

## WAV와 정답 목록

UTF-8 JSONL 한 줄마다 `id`, `audio_path`, 사람이 확인한 `reference`를 기록합니다. `split`, `condition`은 선택이며 기본값은 `evaluation`, `unspecified`입니다. ID는 고유해야 합니다. 예시는 `configs/eval_manifest.example.jsonl`이며 실제 WAV는 포함하지 않았습니다.

```json
{"id":"sample-001","audio_path":"data/evaluation/001.wav","reference":"오늘 날씨가 좋습니다.","split":"evaluation","condition":"clean"}
```

현재 입력은 mono PCM16 WAV이며 샘플레이트는 설정과 일치해야 합니다(기본 16kHz). 자동 resampling·채널 혼합은 하지 않습니다. 경로는 manifest 폴더가 아니라 CLI `--root` 기준이며 기본값은 작업 디렉터리입니다. 절대 오디오 경로도 지원합니다.

최초 동작 확인에는 기존 `0.wav`를 사용할 수 있습니다. 아래는 사용자가 해당 파일을 듣고 정답을 직접 입력해 로컬 목록을 만드는 명령입니다. ASR 출력을 정답으로 자동 사용하지 않습니다. 같은 목록이 있으면 덮어쓰지 않고 중단합니다.

```bash
.venv/bin/python -c '
import json
from pathlib import Path
reference = input("0.wav를 직접 듣고 확인한 정답 문장을 입력하세요: ").strip()
if not reference:
    raise SystemExit("사람이 확인한 정답이 필요합니다.")
path = Path("data/evaluation/manifest.jsonl")
path.parent.mkdir(parents=True, exist_ok=True)
row = {"id": "zipformer-test-0", "audio_path": "models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/test_wavs/0.wav", "reference": reference, "split": "evaluation", "condition": "clean"}
with path.open("x", encoding="utf-8") as handle:
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
print(path)
'
```

한 파일은 도구 동작 확인용입니다. 정확도 결론에는 여러 발화·화자·잡음·길이를 포함해야 합니다. 실제 WAV·정답 목록은 Git 제외 `data/`에 보관합니다.

목록의 모든 행을 실행하며 split 라벨로 자동 필터링하지 않습니다. tuning/evaluation을 함께 넣으면 전체 집계도 양쪽을 포함하므로 공식 비교에는 평가 전용 목록 또는 `by_split.evaluation` 집계를 사용합니다.

## 실행과 결과

```bash
.venv/bin/python scripts/edge_asr.py evaluate \
  --config configs/zipformer_eval.toml \
  --manifest data/evaluation/manifest.jsonl
```

기본값은 2스레드, 모델 로딩 한 번, 최초 유효 WAV로 워밍업 3회 후 각 파일 1회 측정입니다. 매 호출마다 새 인식 상태를 만듭니다. 모델 경로·tokens/encoder/decoder/joiner 파일명·스레드·flush용 무음 길이를 TOML에서 바꿉니다. 기본 무음은 0.66초이며 오디오 길이에는 더하지 않습니다. 기존 `zipformer_streaming.toml`·`ASR_THREADS`는 이 도구에 적용되지 않습니다.

새 결과는 `results/evaluations/eval-<UTC시각>-<임의문자>/`에 생성됩니다. `--output-dir`은 상위 경로만 바꾸며 기존 실행을 덮어쓰지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `manifest.json` | 상태·설정·입력 목록/모델/소스 해시·Git 커밋/변경 여부·모델 준비 시간·워밍업 수 |
| `environment.json` | Python·장비·설치 패키지 버전; pip 없는 uv 환경도 지원 |
| `predictions.jsonl` | 모든 계획 ID의 원본 정답/가설·상태·오디오 메타데이터·계측·실패 사유 |
| `progress.jsonl` | 실행 중 완료한 파일의 기록. 파일마다 flush |
| `scores.jsonl` | 정규화 전후 정답/가설, 편집 수·CER·RTF |
| `summary.json`, `summary.md` | 전체와 split/condition별 집계·성공/실패/미실행·커버리지 |
| `error.txt` | 모델 준비 등 전체 실행 실패 시 traceback |

입력 목록의 오류·중복 ID는 모델 로딩 전에 거부합니다. WAV 누락·형식 오류·파일 추론 실패는 해당 파일을 기록하고 다음 파일을 계속 처리합니다. 워밍업 추론 실패는 남은 평가를 중단합니다. Ctrl-C에서는 완료 결과와 중단·미실행을 저장합니다. 종료 코드는 성공 0, 파일/워밍업 실패 1, 설정·목록·모델 준비 오류 2, 사용자 중단 130입니다. 요약 존재만으로 성공을 판단하지 말고 manifest와 성공/계획 수를 확인합니다.

네이티브 충돌·OOM 강제 종료·하드 타임아웃 격리는 아직 구현하지 않았습니다. 강제 종료에서는 마지막 `progress.jsonl`까지만 남고 manifest가 `running`일 수 있으며 이를 완료로 표시하지 않습니다.

## 측정 계약

- `ko_cer_v1`: Unicode NFC → 공백 제거 → Unicode 범주 P 문장부호 제거. 숫자·영문 대소문자는 유지합니다. `nfc_v1`은 NFC만 적용합니다. 코드포인트 단위 편집 거리를 사용하며 동률 경로는 대각선·삭제·삽입 순서입니다.
- Corpus CER은 성공한 비어 있지 않은 정답의 편집 수 합 / 정답 문자 수 합입니다. 파일 CER의 단순 평균이 아닙니다. 빈 정답 CER은 `null`이며 해당 샘플/비어 있지 않은 가설 수를 별도 집계합니다. 실패를 제외하므로 성공률·정답 문자 커버리지를 함께 해석합니다.
- Corpus RTF는 성공 파일의 추론 wall time 합 / 실제 오디오 길이 합입니다. WAV 읽기·해시·float 변환·모델 로딩·워밍업·채점·파일 쓰기를 제외하고 새 스트림 생성·PCM 전달·특징 추출·디코딩·무음 flush·최종 반환을 포함합니다. 모델 준비 시간은 런타임 import·인식기 초기화 포함으로 별도 기록합니다.
- CPU는 추론 구간의 프로세스 CPU 시간 / wall time × 100이며 코어 하나가 100%입니다. 추론 중 RSS 계측 스레드 비용도 포함합니다. RSS는 모델 상주 상태의 주 PID를 100ms 간격과 호출 전후 경계에서 관측합니다. 정확한 피크·로딩 피크·자식 메모리를 보증하지 않으며 수집 불가는 `null`과 누락 수로 남깁니다.
- 파일 처리 시간 분포는 길이가 다른 WAV의 처리 시간 분포입니다. 부분 자막·실제 인식 지연·endpoint·드롭·백로그는 미계측 `null`이며 처리 시간으로 대체하지 않습니다. 기존 프로브의 측정 범위와 구분합니다.

## 재채점

평가 완료 후 출력된 실제 경로로 `RUN_DIR`을 바꿉니다.

```bash
RUN_DIR=results/evaluations/실제_출력된_실행폴더
.venv/bin/python scripts/edge_asr.py score \
  --predictions "$RUN_DIR/predictions.jsonl" --normalization ko_cer_v1
```

모델·WAV를 열거나 추론 라이브러리를 import하지 않고 `results/scores/score-.../`에 새 결과를 만듭니다. 기존 가설·처리 시간을 재집계하며 새 장비에서 속도를 재측정한 것이 아닙니다. 원래 실행 폴더는 수정하지 않습니다.

정답을 고쳤다면 `--references data/evaluation/corrected.jsonl`을 지정합니다. ID 집합이 원래 결과와 같아야 하며 reference만 교체합니다. 원래 split/condition·오디오·가설·시간은 유지합니다. 다른 오디오의 정답을 연결하지 않도록 ID를 유지하세요. 새 정규화 이름·교체 목록 해시는 재채점 출처에 기록합니다.

Unicode 데이터베이스 버전도 집계에 기록하며 재채점 출처에는 채점 Python과 채점기 소스 해시를 남깁니다. 서로 다른 정책·Unicode 버전의 CER을 같은 조건으로 취급하지 않습니다.

## 모델 확장

추상 기반 클래스·플러그인 시스템 없이 어댑터의 `model_files`, `load()`, `transcribe(Audio) -> str`, `close()` 경계를 사용합니다. 새 모델 모듈과 `adapters.create_adapter` 분기, 필요한 전용 설정 검증을 추가하면 데이터·채점·저장은 재사용합니다. 미구현 모델은 명시적으로 실패합니다.

Zipformer 호출은 보존 프로브와 [sherpa-onnx 공식 파일 디코딩 예제](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/online-decode-files.py)를 참고했습니다. 실제 RP5 검증은 새 실행기로 수행해야 합니다.
