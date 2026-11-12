# 개발 안내

먼저 [README](README.md), [현재 상태](docs/status.md), [확인한 환경](docs/reference/environment.md)을 읽습니다. 코드 변경 원칙은 [AGENTS.md](AGENTS.md), 의존 방향은 [아키텍처](docs/reference/architecture.md)를 따릅니다.

## 시작하기

1. [설치 안내](docs/guides/setup.md)에 따라 새 Host 작업 폴더에 Python 3.11 개발 환경을 준비합니다. 기존 `.venv`가 있으면 재생성하지 않습니다.
2. 아래 Host 검사를 실행합니다. 모델·평가 음원·RP5 접속 없이 코드 작업을 시작할 수 있습니다.
3. 실제 추론이 필요하면 [현재 GGUF 실행 안내](docs/guides/qwen3_optimization.md#현재-개발용-대표-명령)를 따릅니다. 모델·음원·native 빌드는 clone에 포함되지 않습니다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
ruff check src tests scripts native
ruff format --check src tests scripts native
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf_realtime.toml
```

새 개발 환경에 `.[dev]`를 설치하면 Ruff도 그 환경에 설치됩니다. 위 `ruff`는 해당 환경을 활성화하거나 `.venv/bin/ruff`로 실행합니다. 현재 Host는 별도 설치된 Ruff를 사용합니다.

## 수정할 위치

| 작업 | 위치 |
| --- | --- |
| Qwen 모델 호출·연속 입력 | `src/edge_asr/qwen/`, `tests/qwen/` |
| C++ 연결·빌드 | `native/gguf/`, `native/ort_trace/` |
| 입력·채점·계측·기록 | 기존 `dataset.py`, `scoring.py`, `metrics.py`, `resources.py`, `runtime.py`, `artifacts.py` |
| 데이터 준비·실행 제한·보고서 | `src/edge_asr/tools/`의 해당 역할 폴더 |
| 실험 조건 | `configs/`의 별도 설정과 실행 명령. 기존 기준선은 보존 |
| 이전 Zip·모델 비교 재현 | `src/edge_asr/legacy/`, `tests/legacy/` |

공통 코드와 Qwen은 `legacy`를 import하지 않습니다. `adapters/`의 공통 factory와 일반 평가에 쓰는 이전 모델 어댑터는 유지합니다. 새 실험마다 실행기를 복제하지 않고 기존 평가기를 재사용합니다.

## 변경·검증 순서

작업 브랜치에서 구현·Host 검사 → main 반영·push → RP5 소스 동기화 → 필요한 실제 추론 → 결과 회수 순서로 진행합니다. 구조 이동과 추론 동작 변경은 별도로 검증합니다.

변경 기록에는 변경 이유, 사용한 검사, 생략한 검사와 이유, 실제 RP5 추론 여부를 적습니다. PCM 형식·부분/확정 이벤트·실패 기록·`ko_cer_v1`·native ABI를 바꾸면 해당 호출부와 검증을 함께 검토합니다. 경로를 바꾸면 import·CLI·테스트·문서 링크도 갱신합니다.

| 변경 | 필요한 검증 |
| --- | --- |
| 문서·안내 | 문서 링크·예시 명령 확인. 모델 추론 불필요 |
| CLI·경로 이동·공통 코드 | 관련 테스트와 전체 Host 회귀 검사·Ruff·보존 검사 |
| 모델 호출·분할·계측·C++ | Host 검사 후 같은 main 커밋으로 RP5에서 필요한 실제 추론·출력/계측 비교 |

[Host CI](.github/workflows/host.yml)는 PR과 main push에서 Python 3.11 테스트·Ruff·M1 원본 해시·설정 검사를 실행합니다. 이는 모델 없는 검사이며 ARM 추론 성능을 보증하지 않습니다. 선택 native 헤더 검사와 실제 추론의 범위는 [테스트 안내](tests/README.md)를 따릅니다.

## RP5 실행과 결과 기록

- RP5는 실제 추론을 한 번에 하나씩 실행합니다. 실행 중 pull·checkout·패키지 변경을 하지 않습니다.
- 실행 ID는 목적·날짜·순번으로 정하고 새 출력 폴더를 사용합니다. 예: `realtime-example-20261111-01`.
- Git은 소스·공유 설정·문서·검토한 요약을 전달합니다. 모델·원음·native 빌드·원시 결과는 필요한 경로만 SSH/rsync로 전달하고 해시를 확인합니다.
- 요약에는 소스 커밋, 설정·명령, 모델/빌드/입력 해시, 환경, 실패·생략·미측정 범위를 남깁니다. 과거 실행 기록의 경로·해시·판정을 소급 수정하지 않습니다.

자산 위치와 장비 차이는 [환경 기록](docs/reference/environment.md), 동기화 절차는 [RP5 안내](docs/guides/deployment.md)를 확인합니다.
