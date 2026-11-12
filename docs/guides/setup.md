# 개발 환경 준비

현재 프로젝트는 Docker 없이 **Host 개발용 가상환경과 RP5 추론용 가상환경**을 사용합니다. 직접 확인한 OS·Python·패키지·외부 엔진 버전은 [환경 기록](../reference/environment.md), 개발 절차는 [CONTRIBUTING](../../CONTRIBUTING.md)을 확인합니다.

## 기존 환경에서 시작

이미 `.venv`가 있는 현재 Host와 RP5는 재생성·재설치 없이 체크아웃의 명령을 사용합니다.

```bash
.venv/bin/python --version
.venv/bin/python scripts/edge_asr.py env
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf_realtime.toml
.venv/bin/python scripts/edge_asr.py tools list
```

Host의 모델 없는 검사에는 추론 패키지가 필요하지 않습니다. 실제 모델 실행·성능 판단은 RP5에서 수행합니다. 모델·음원·런타임은 자동으로 내려받지 않습니다.

## 새 Host 개발 환경

아래는 **새 clone에 `.venv`가 없을 때만** 실행합니다. Python 3.11 인터프리터를 먼저 준비하고 버전을 확인합니다. 현재 Host의 기본 `/usr/bin/python`은 3.10이므로 사용하지 않습니다. `python3.11`이 PATH에 없다면 별도 3.11 실행 경로를 지정합니다. 현재 Host의 3.11은 uv 관리 인터프리터입니다.

```bash
python3.11 --version
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src tests scripts native
.venv/bin/ruff format --check src tests scripts native
```

Linux 셸을 기준으로 합니다. 기본 코드와 테스트는 Python 3.11 표준 라이브러리를 사용하고 `dev` extra는 Ruff 0.14.14를 설치합니다. 별도 `ffmpeg`가 있으면 합성 음원 변환 검사도 실행되며, 없으면 해당 검사의 생략 이유를 확인합니다. native 검사에 필요한 선택 헤더와 명령은 [테스트 안내](../../tests/README.md)에 있습니다.

기존 Host에는 Ruff가 사용자 도구로 설치되어 있어 `ruff` 명령을 사용합니다. 기존 `.venv`에 이를 다시 설치할 필요는 없습니다. 모델 다운로드·RP5 접속 없이 이 단계까지 개발을 시작할 수 있습니다.

## RP5 실제 추론 준비

현재 RP5는 Debian 13·ARM64·별도 Python 3.11.18 환경입니다. 기본 `python3`는 3.13.5이며 프로젝트 실행에는 기존 `.venv/bin/python`을 명시합니다. [소스 동기화 안내](deployment.md)로 main 커밋을 맞춘 뒤 [Qwen 대표 명령](qwen3_optimization.md#현재-개발용-대표-명령)을 사용합니다.

| 필요한 항목 | 현재 경로·준비 안내 |
| --- | --- |
| Python 환경 | 현재 `.venv`의 패키지는 [환경 기록](../reference/environment.md#가상환경별-용도) 참고 |
| GGUF 모델·자산 명세 | [공유 설정](../../configs/gguf_realtime.toml)의 `model.path`, [GGUF 자산·평가 계약](../research/opt2_gguf.md) |
| GGUF bridge·빌드 명세 | [공유 설정](../../configs/gguf_realtime.toml)의 `model.runtime_library`·`model.runtime_manifest`, [현재 빌드 안내](qwen3_optimization.md#환경과-설정), [native 빌드](../../native/README.md) |
| 확률 VAD 모델 | `models/silero-vad-k2-20261029/silero_vad.onnx`, [VAD 준비 기록](../archive/model-comparison/m31_online_vad.md) |
| 평가 음원·정답 | `data/evaluation/edge100-20261109-01/`, [고정100개 준비·검증](../research/fixed100.md) |
| 모델 설정·연속 입력 옵션 | [configs/gguf_realtime.toml](../../configs/gguf_realtime.toml), [대표 명령](qwen3_optimization.md#현재-개발용-대표-명령) |

새 RP5는 clone만으로 추론할 수 없습니다. Python 3.11·필요한 패키지·모델·음원·고정 엔진을 별도로 준비하고 해시·설정·빌드를 확인합니다. 장비나 빌드 경로가 달라지면 별도 설정·빌드 ID를 사용합니다. Host 가상환경이나 x86_64 바이너리를 ARM64로 복사하지 않습니다.

## 새 후보 환경과 과거 재현

새 후보 환경이 필요한 경우에만 기존 환경과 다른 이름으로 만듭니다. 의존성은 [pyproject.toml](../../pyproject.toml)의 선택 extra로 구분합니다.

| extra | 용도 |
| --- | --- |
| `dev` | Host 정적 검사 |
| `vad-probability` | NumPy·Python ONNX Runtime 기반 VAD. GGUF 엔진은 별도 native 빌드 |
| `qwen3` | Qwen ONNX용 sherpa-onnx·NumPy |
| `zipformer`, `sensevoice` | 이전 비교 모델 재현 |

현재 GGUF+VAD는 준비된 `.venv`를 그대로 사용합니다. ONNX 전용 `.venv-qwen3-onnx-20261106-01`과 ONNX+VAD `.venv-qwen3-vad-20261106-01`도 보존합니다. 선택 extra의 직접 의존성 고정만으로 전체 환경 lock이 되지는 않으며 새 환경의 설치·로딩·실제 추론은 별도로 확인해야 합니다.

기존 ONNX 환경의 다운로드·wheel 해시·설치·freeze 기록은 `results/maintenance/qwen3-20261106-01/`, 별도 ONNX+VAD 환경 기록은 `results/maintenance/qwen3-realtime-20261106-01/`에 있습니다. [이전 Qwen 설치 기록](../archive/model-comparison/qwen3_deployment.md)을 참고합니다. 새 환경 기록은 [환경 문서 갱신 절차](../reference/environment.md#환경-기록-갱신)를 따릅니다.
