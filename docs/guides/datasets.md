# 평가 데이터 준비

오디오·전사·준비 이력은 Git 제외 `data/evaluation/`에 보관합니다. Host에서 다운로드·형식 변환·검증하고 RP5에는 검증한 파일을 전달합니다. 추론은 RP5에서 수행합니다. [공통 평가 안내](evaluation.md)의 JSONL과 SHA-256 검증을 사용합니다.

## 첫 공식 샘플과 KSS 개발용 50개

`scripts/edge_asr.py tools prepare-official`는 기존 모델의 공식 `0.wav`를 수정 없이 검사하여 첫 동작 확인 목록을 만듭니다. 아래 50개 목록은 여러 발화에서의 개발 기준선을 준비하는 별도 작업입니다.

| 항목 | KSS 개발용 50개 설정 |
| --- | --- |
| 배포처 | [Bingsu/KSS_Dataset](https://huggingface.co/datasets/Bingsu/KSS_Dataset) |
| 고정 리비전 | `48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c` |
| 선택 | `default/train`의 0부터 시작하는 행 번호 1000~1049, 연속 50개 |
| 선택 기준 | 인식 결과를 보기 전에 고정한 작은 개발용 구간. 무작위·대표 표본이 아님 |
| 정답 | 배포처의 `expanded_script`: 숫자 등을 읽는 형태로 확장한 전사. ASR 출력으로 정답을 만들지 않음 |
| 화자·발화 | 전문 여성 화자 1명의 낭독. 다화자·잡음·자발화 평가를 대신하지 않음 |
| 역할 | 프로젝트의 `development` split. 배포처의 `train`에서 가져왔으며 독립 test split이 아님 |
| 원본/입력 | 원본 44,100Hz mono PCM16 WAV를 별도 보존하고 Host ffmpeg로 16,000Hz mono PCM16 WAV 생성 |

데이터 설명·전사 필드와 라이선스는 [고정한 데이터 카드](https://huggingface.co/datasets/Bingsu/KSS_Dataset/blob/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/README.md)를 따릅니다. 데이터 라이선스는 **CC BY-NC-SA 4.0**이며 비상업 조건이 있습니다. 원저자 Kyubyong Park, KSS Dataset(2018)의 출처와 데이터 카드를 로컬 준비 자료에 함께 보관합니다. 데이터의 이용 조건은 저장소 코드와 별개입니다.

모델 학습 데이터와의 중복 여부는 확인하지 않았습니다. 이 50개로 한국어 전체 정확도·화자 일반화·최종 test 성능을 주장하지 않습니다. 이후 최종 평가에는 튜닝과 분리된 데이터, 여러 화자·장치·잡음·길이·숫자/외래어 조건이 필요합니다.

## Host에서 준비

Python 3.11 표준 라이브러리와 Host의 `ffmpeg` 실행 파일을 사용합니다. `datasets`·`pyarrow`·추론 런타임의 추가 설치가 필요하지 않습니다. ffmpeg 설치는 Host의 패키지 관리자를 사용하며 이 도구는 패키지를 자동 설치하지 않습니다.

```bash
cd /d/shin/edge-asr &&
.venv/bin/python scripts/edge_asr.py tools prepare-kss
```

필요하면 `--root /프로젝트/루트`, `--ffmpeg /경로/ffmpeg`를 지정합니다. 기본 준비 경로는 `data/evaluation/kss-dev50/`입니다. `--output-dir`로 바꾸는 경우에도 프로젝트의 `data/` 내부를 사용합니다.

Hugging Face [Dataset Viewer `/rows`](https://huggingface.co/docs/dataset-viewer/rows)에서 50개 행과 오디오만 받습니다. 서버 `X-Revision`과 각 오디오 URL의 리비전·행 번호를 검사하므로 배포처가 바뀌면 고정 버전을 자동 교체하지 않고 중단합니다. 일시적인 네트워크 오류는 최대 3회 시도하고, 실패한 준비는 최종 목록으로 발행하지 않습니다. 동일 경로에서 준비 명령을 다시 실행하면 모든 기록된 파일 해시를 확인하여 네트워크·재변환 없이 재사용합니다. 기존 데이터가 다르거나 불완전하면 덮어쓰지 않습니다. 준비 명령은 동시에 실행하지 않습니다.

```text
data/evaluation/kss-dev50/
├── original/          # 받은 44.1kHz WAV, 내용 보존
├── wav/               # 평가용 16kHz WAV
├── manifest.jsonl     # 발화 ID·전사·상대 WAV 경로·예상 SHA-256
├── provenance.json    # 선택 규칙·리비전·원본/변환 해시·길이·ffmpeg 버전
├── source_rows.json   # 배포처 응답 스냅샷, 원본 전사 포함
└── SOURCE_README.md   # 고정 리비전의 원저자·출처·이용 조건
```

ffmpeg 버전이 달라지면 변환 결과가 달라질 수 있습니다. 모델·설정을 비교할 때에는 새로 변환하지 말고 동일한 준비 폴더와 manifest·WAV 해시를 사용합니다. 원본·변환본·전사·개별 인식 결과는 Git에 올리지 않고 검토한 작은 수치 요약만 버전 관리합니다.

## RP5로 전달하고 실행

Host에서 대상 경로가 없는지 확인한 뒤 한 번 전달합니다. `rsync --ignore-existing`는 기존 파일을 덮어쓰지 않으므로 복사가 끝난 후 준비 도구의 재사용 검사로 누락·불일치를 확인합니다. 부분 복사나 다른 내용이 발견되면 자동 삭제·교체하지 않고 원인을 확인합니다. 소스는 먼저 [배포 안내](deployment.md)에 따라 최신 커밋으로 맞춥니다.

```bash
ssh rp5 'mkdir -p /home/deepet/projects/edge-asr/data/evaluation' &&
rsync -r --ignore-existing \
  data/evaluation/kss-dev50 \
  rp5:/home/deepet/projects/edge-asr/data/evaluation/ &&
ssh rp5 'cd /home/deepet/projects/edge-asr &&
  .venv/bin/python scripts/edge_asr.py tools prepare-kss &&
  .venv/bin/python scripts/edge_asr.py evaluate \
    --manifest data/evaluation/kss-dev50/manifest.jsonl'
```

이때 RP5의 준비 명령은 기존 자료의 해시만 확인하므로 ffmpeg·네트워크를 사용하지 않습니다.

실행 설정은 기존 `configs/zipformer_eval.toml`의 2스레드, 첫 발화로 워밍업 3회, 50개 파일 각 1회입니다. 파일마다 인식 상태를 새로 만들고 모델은 한 번만 로드합니다. CER·batch RTF·CPU/RSS는 전체와 development split으로 집계합니다. 파일 처리 시간 분포는 길이가 다른 50개 발화의 분포이며 같은 발화의 30회 반복이나 스트리밍 지연의 p50/p95가 아닙니다.

결과 폴더는 `results/evaluations/eval-.../`입니다. manifest `completed`, 성공 50/50, 워밍업 3/3, 실패·미실행 0을 확인하고 Host에서 결과 회수·체크섬 대조·재채점을 수행합니다. 모델·기존 RP5 자산·과거 실험 결과는 변경하지 않습니다.
