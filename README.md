# edge-asr

Raspberry Pi 5(ARM64, Debian 13, RAM 8GB)에서 한국어 ASR 모델을 비교하고 실시간 배포하기 위한 연구 저장소입니다. **Host에서 개발하고 RP5에서 실제 추론·성능을 검증합니다.** 기준 Python은 3.11이며 모델·오디오·가상환경·원시 결과는 각 장비에 보존합니다.

Zipformer INT8의 batch/1배속 모사와 SenseVoiceSmall INT8의 batch 평가를 구현했습니다. 공통 입력·CER·RTF·CPU/RSS·실패 기록·재채점을 사용합니다. 현재는 VAD와 발화 구간 정책을 검증하는 M3 단계입니다. [현재 상태·다음 작업](docs/status.md), [전체 문서](docs/README.md), [실측 기록](results/summaries/README.md)을 참고하세요.

## 빠른 시작

이미 준비된 Host/RP5 `.venv`를 사용합니다. 새 환경 설치는 [설치 안내](docs/guides/setup.md)를 따릅니다. Debian 13의 시스템 Python을 프로젝트의 3.11로 가정하지 않습니다.

```bash
# 저장소 루트에서 실행. 기존 RP5 환경에는 재설치 불필요.
.venv/bin/python scripts/edge_asr.py env
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/zipformer_eval.toml
.venv/bin/python scripts/edge_asr.py tools list

# 준비된 자료가 있을 때 RP5에서 실제 평가
.venv/bin/python scripts/edge_asr.py evaluate \
  --config configs/zipformer_eval.toml \
  --manifest data/evaluation/kss-dev50/manifest.jsonl
```

설치된 환경에서는 `edge-asr` 또는 `python -m edge_asr`도 같은 CLI입니다. 평가용 WAV/정답 목록이 없으면 [데이터 준비](docs/guides/datasets.md)부터 진행합니다. 데이터 준비·VAD 진단·결과 집계는 `edge-asr tools <명령>`에 모았으며, [이전 scripts 명령 이행표](docs/reference/tooling.md)를 제공합니다.

## 폴더와 코드 역할

```text
src/edge_asr/
  adapters/          Zipformer·SenseVoice 모델별 호출
  tools/
    data/            데이터 준비·검수 결정 반영
    probes/          RP5 측정·외부 온도/시간 제한
    reports/         원시 검증·추론 없는 집계
    maintenance/     과거 작업 폴더 통합
  artifacts.py       공통 해시·JSON 산출물
  resources.py       프로세스 CPU/RSS 표본
  dataset.py         manifest·PCM16 WAV
  evaluation.py      공통 ASR 실행
  streaming.py       독립 입력·유한 큐·1배속 모사
  segmentation.py    공통 PCM 버퍼·발화 구간 상한
configs/             현재 모델 설정; legacy/는 M1 탐색 설정
scripts/             공통 CLI·M1 반복·선택 배포 진입점
benchmarks/          평가 입력·측정 명세
experiments/         보존 원본·참고 코드
results/summaries/   버전 관리하는 작은 검증 보고서
models/, data/       로컬 모델·오디오·검수 자료
results/             로컬 원시 실행·회수 기록
```

공통 패키지와 모델 어댑터는 실행 도구를 import하지 않습니다. 도구는 공통 계약을 재사용하며 선택한 명령만 지연 import합니다. [구조와 확장 경계](docs/reference/architecture.md), [지표 정의](docs/reference/benchmark_design.md).

## 개발·검증·동기화

```bash
# Host, 모델 없이 전체 회귀 검사
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
# Host의 선택 개발 도구: pip install -e '.[dev]'
ruff check src tests scripts
ruff format --check src tests scripts
# 원본 확인
sha256sum -c experiments/m1_zipformer/SHA256SUMS
sha256sum -c experiments/m1_zipformer/reference/SHA256SUMS
```

Host `/d/shin/edge-asr`와 RP5 `/home/deepet/projects/edge-asr`는 **main 하나**로 관리합니다. Host에서 commit/push한 뒤 RP5에서 `git pull --ff-only origin main`으로 소스를 갱신합니다. 모델·자료·원시 결과는 Git에 포함하지 않고 새 경로에 별도로 전달해 해시를 확인합니다. [배포·보존 절차](docs/guides/deployment.md), [테스트 범위](tests/README.md).

## 현재 확인한 범위

- M1/M2: Zipformer batch·paced_stream과 SenseVoice batch를 RP5에서 실행하고 Host 재채점을 대조했습니다. 모델 간 비교에는 같은 자료·정규화·실행 조건과 각 기록의 한계를 확인해야 합니다.
- M3: 공개 자료 98개 중 18개를 보류하고 개발 80개 × 원음/RMS로 실제 VAD 160회를 확인했습니다. 배포 라벨 예비 비교에서 초단문 전체 누락은 9/64·4/64였으나 RMS의 새 누락과 비음성 검출 증가가 남았습니다. [예비 결과](results/summaries/m3-vad-provisional-20261029.md).
- 다음 과제: 실제 청취/파형 검수, native 활성 30초 한계, 고부하 냉각 조건입니다. 실제 endpoint·Hybrid 동시 실행·마이크·Qwen3-ASR은 후속 범위입니다. 기존 단독 batch 수치로 동시 성능을 추정하지 않습니다.

초기 Zipformer 3.526초 WAV의 1/2/4스레드 결과는 **동일 단일 샘플의 각 1회 측정**입니다. [보존 기록](experiments/m1_zipformer/README.md)에서 출처와 한계를 확인할 수 있습니다. 과거 보고서의 수치·환경을 현재 조건으로 소급 변경하지 않습니다.
