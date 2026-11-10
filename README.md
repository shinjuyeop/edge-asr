# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

한국어 정확도·완료율을 유지하면서 Qwen3-ASR-0.6B의 RP5 인식 지연·메모리·발열을 줄이는 프로젝트입니다. 현재 ONNX INT8/GGUF Q8_0의 파일 기반 batch 평가까지 구현했습니다. 발화 중 부분 자막·마이크 검증은 후속입니다.

## 먼저 볼 문서

| 내용 | 바로가기 |
| --- | --- |
| 지금 상태와 다음 작업 | [status](docs/status.md) |
| 현재 비교 기준값 | [고정100개 실측](results/summaries/optimization/fixed100-baseline-20261109.md) |
| 평가 조건·개선 판정 | [기준선 프로토콜](docs/research/fixed100_baseline.md) |
| 음성100개의 출처·선정 | [데이터 설명](docs/research/fixed100.md) |
| 실행 명령·별도 환경 | [실행 안내](docs/guides/qwen3_optimization.md) |
| 전체 연구 방향 | [OPT-0~4](docs/research/qwen3_optimization.md) |

최근 GGUF 2T는100/100 성공·CER7.67%·RTF0.6225이며 이후 GGUF 변경의 기준값입니다. ONNX 출발 참조는99/100 성공으로 전체 정확도 미완료입니다. 같은 성공99개 비교·온도·실패·한계는 실측 보고서에서 확인하세요.

## 코드 읽는 순서

**benchmark.py → gguf.py → bridge.cpp** 순서로 따라가면 됩니다.

| 역할 | 파일 |
| --- | --- |
| 평가 진행: 로딩·첫 추론·워밍업·반복·기록 | [qwen/benchmark.py](src/edge_asr/qwen/benchmark.py) |
| GGUF Python 연결·입출력 검사 | [qwen/gguf.py](src/edge_asr/qwen/gguf.py) |
| llama.cpp/libmtmd 호출·KV·토큰 생성 | [native/gguf/bridge.cpp](native/gguf/bridge.cpp), [빌드 안내](native/README.md) |
| ONNX 출발 참조·context 검사 | [qwen/onnx.py](src/edge_asr/qwen/onnx.py) |
| ONNX 호출 경계 계측 | [qwen/profiling.py](src/edge_asr/qwen/profiling.py) |
| 공통 평가·채점 | [evaluation.py](src/edge_asr/evaluation.py), [scoring.py](src/edge_asr/scoring.py), [metrics.py](src/edge_asr/metrics.py) |
| 자원·native 진단 | [resources.py](src/edge_asr/resources.py), [runtime.py](src/edge_asr/runtime.py), [열 실행기](src/edge_asr/tools/probes/thermal.py) |

명령 진입점은 [scripts/edge_asr.py](scripts/edge_asr.py), 도구 선택은 [tools/cli.py](src/edge_asr/tools/cli.py)입니다. 자세한 의존 방향은 [아키텍처](docs/reference/architecture.md), 검증 방법은 [테스트 안내](tests/README.md)에 있습니다.

## 폴더 구조

```text
src/edge_asr/
├── qwen/             # onnx.py, gguf.py, benchmark.py, profiling.py
├── tools/data/       # 데이터 준비·검증·고정 평가 순서
├── tools/probes/     # 열·시간·메모리 제한
├── adapters/         # 이전 비교 모델 연결과 공통 factory
├── legacy/           # 이전 Zip+VAD+Refine·자막·병합 재현
└── *.py              # 공통 입력·설정·평가·채점·계측
native/               # gguf/ C ABI, ort_trace/ 계측 소스·빌드
configs/              # onnx.toml, gguf.toml, legacy/
tests/                # qwen/, common/, legacy/
docs/                 # 계획·실행 안내·구조·보관 문서
results/summaries/     # Git에 보관하는 실측·검증 요약
data/, models/        # 로컬 음성·정답·가중치
runtimes/             # 로컬 외부 엔진·빌드·라이브러리
results/optimization/ # 로컬 원시 측정값
results/maintenance/  # 로컬 테스트·보존·전송 검증
experiments/          # M1 원본 보관
```

현재 설정은 [gguf.toml](configs/gguf.toml)과 [onnx.toml](configs/onnx.toml)입니다. 실제 입력은 로컬 `data/evaluation/edge100-20261109-01/manifest.jsonl`이며 [예시 JSONL](configs/eval_manifest.example.jsonl)은 형식만 보여 줍니다. 음원·가중치·원시 결과는 GitHub에 포함하지 않습니다.

## 빠른 확인

아래는 모델 추론 없이 확인하는 명령입니다.

```bash
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf.toml
.venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 --help
.venv/bin/python scripts/edge_asr.py tools prepare-fixed100 --verify-only
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

작업 순서는 **Host 개발·테스트 → main commit/push → RP5 소스 동기화·실측**입니다. 현재 명령과 새 실행 ID는 [실행 안내](docs/guides/qwen3_optimization.md)를 따릅니다.

[전체 문서](docs/README.md) · [최적화 결과](results/summaries/optimization/README.md) · [이전 모델 비교·자막](docs/archive/model-comparison/README.md). 이전 `live`와 설정 없는 `evaluate`의 Zipformer 기본값은 유지합니다. 과거 경로는 [구조 이행표](docs/reference/structure_migration.md)에서 확인하세요.
