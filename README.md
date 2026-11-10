# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B를 Raspberry Pi 5의 ARM64 CPU에서 실행하며, **한국어 정확도와 처리 완료율을 유지하면서 인식 지연·메모리·발열을 줄이는** 프로젝트입니다.

[현재 상태·다음 작업](docs/status.md) · [평가 기준](docs/research/fixed100_baseline.md) · [실측 결과](results/summaries/optimization/fixed100-baseline-20261109.md) · [실행 안내](docs/guides/qwen3_optimization.md)

## 현재 진행 상태

- **구현·검증:** ONNX INT8 / GGUF Q8_0 batch 추론, 고정100개 평가, ONNX 호출 경계 프로파일링, CPU·RSS·온도·클럭 계측
- **진행 방향:** 기존 ONNX를 출발 참조로 두고, GGUF 2T 기준선 대비 실행 설정과 추론 경로 최적화
- **후속 구현·검증:** 발화 중 부분 자막, 사용자 TTFT, 마이크 입력, 최종 장시간 안정성

현재는 WAV 파일로 검증합니다. 이전 Zip+VAD+Refine 자막은 `legacy/`에 보존합니다.

## 현재 GGUF 기준값

RP5 · Qwen3-ASR-0.6B Q8_0 · CPU 2스레드 · **2026-11-09 실측**입니다.

| 처리 성공 | 문자 오류율 CER ↓ | 처리 시간 비율 RTF ↓ | 관측 최대 RSS |
| ---: | ---: | ---: | ---: |
| 100/100 | 7.67% | 0.6225 | 1772.7 MiB |

입력은 Zeroth60개·FLEURS40개, 총14분51.145초이며 [출처·선정 방법](docs/research/fixed100.md)을 고정했습니다. 이후 GGUF 최적화는 이 구성과 같은 입력으로 비교합니다.

이 값은 batch 처리 지표이며 실제 자막 반환 지연·TTFT와 구분합니다. ONNX 참조는 기존 context 검사 거부1건으로99/100 성공했습니다. 같은 성공 부분집합 비교와 온도·재부팅 조건, 실패 원시는 [실측 보고서](results/summaries/optimization/fixed100-baseline-20261109.md)에 있습니다.

## 빠른 확인

Python3.11 이상이 필요합니다. 아래 명령은 저장소 루트의 **기존 `.venv` 환경**을 기준으로 하며 모델을 로딩하지 않습니다.

```bash
# 설정과 평가 명령 확인
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf.toml
.venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 --help

# Host 모의 테스트
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

실제 모델 실행에는 별도로 준비한 가중치·런타임이 필요합니다. ONNX/GGUF별 RP5 환경과 평가 명령은 [실행 안내](docs/guides/qwen3_optimization.md), C++ 연결·빌드는 [native 안내](native/README.md)를 따릅니다. 로컬에100개 평가 자료가 준비되어 있다면 `.venv/bin/python scripts/edge_asr.py tools prepare-fixed100 --verify-only`로 검사할 수 있습니다.

## 코드 길잡이

GGUF 실행 흐름은 **평가 실행기 → Python 어댑터 → C++ bridge → llama.cpp/libmtmd**입니다.

| 목적 | 먼저 볼 파일 |
| --- | --- |
| 모델 로딩·첫 추론·워밍업·반복 평가 | [qwen/benchmark.py](src/edge_asr/qwen/benchmark.py) |
| GGUF 연결·음성 전달·출력 검사 | [qwen/gguf.py](src/edge_asr/qwen/gguf.py) |
| KV 초기화·오디오/prefill·토큰 생성 제어 | [native/gguf/bridge.cpp](native/gguf/bridge.cpp) |
| ONNX 참조·context 검사 / 호출 경계 계측 | [qwen/onnx.py](src/edge_asr/qwen/onnx.py) / [qwen/profiling.py](src/edge_asr/qwen/profiling.py) |
| 음성 목록·WAV 로딩 / 일반 파일 평가 | [dataset.py](src/edge_asr/dataset.py) / [evaluation.py](src/edge_asr/evaluation.py) |
| 저장 가설 재채점·CER/RTF 집계 | [scoring.py](src/edge_asr/scoring.py), [metrics.py](src/edge_asr/metrics.py) |
| CPU/RSS·native 진단 / 온도·실제 클럭 | [resources.py](src/edge_asr/resources.py), [runtime.py](src/edge_asr/runtime.py) / [thermal.py](src/edge_asr/thermal.py) |

설정은 [configs/gguf.toml](configs/gguf.toml)과 [configs/onnx.toml](configs/onnx.toml)에 있습니다. 명령 진입점은 [scripts/edge_asr.py](scripts/edge_asr.py), 상세 의존 방향은 [아키텍처](docs/reference/architecture.md)를 참고하세요.

## 폴더 구조

```text
src/edge_asr/
├── qwen/             # Qwen 추론·기준선 실행·프로파일링
├── tools/            # 데이터 준비·열/자원 제한·CLI 도구
├── legacy/           # 이전 비교·자막·병합 재현
└── *.py              # 공통 입력·설정·평가·채점·계측
native/               # C++ bridge·ORT 계측·빌드 도구
configs/              # 현재 ONNX/GGUF 설정과 legacy 설정
tests/                # qwen/ · common/ · legacy/
docs/                 # 현재 계획·실행 안내·구조·보관 문서
results/summaries/    # Git에 보관하는 실측·검증 요약
```

음성·정답은 `data/`, 가중치는 `models/`, 외부 엔진·빌드는 `runtimes/`, 원시 측정·검증 로그는 `results/optimization/`·`results/maintenance/`에 보관합니다. 이 로컬 자산들은 GitHub에 포함하지 않습니다. [예시 JSONL](configs/eval_manifest.example.jsonl)은 목록 형식만 보여 주며 실제 평가 자료는 아닙니다.

## 개발·평가 흐름

**Host 개발·모의 테스트 → main commit/push → RP5 소스 동기화·실측 → 결과 회수·재채점** 순서로 진행합니다. 새 실험은 별도 설정·실행 ID를 사용하고 기존 환경·모델·원음·결과를 보존합니다. Host 테스트와 RP5 실제 추론을 구분하며, 미측정 값은 추정으로 채우지 않습니다.

[전체 문서](docs/README.md) · [연구 로드맵](docs/research/qwen3_optimization.md) · [테스트 안내](tests/README.md) · [전체 OPT 기록](results/summaries/optimization/README.md) · [이전 연구](docs/archive/model-comparison/README.md) · [경로 이행표](docs/reference/structure_migration.md)
