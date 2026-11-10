# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B를 Raspberry Pi 5의 ARM64 CPU에서 실행하며, **한국어 정확도와 처리 완료율을 유지하면서 인식 지연·메모리·발열을 줄이는** 프로젝트입니다.

[현재 상태·다음 작업](docs/status.md) · [연속 입력 평가 기준](docs/research/qwen_realtime.md) · [실시간 결과](results/summaries/optimization/realtime100-q8-20261110.md) · [실행 안내](docs/guides/qwen3_optimization.md)

## 현재 진행 상태

- **구현·검증:** ONNX INT8 / GGUF Q8_0 batch 추론, 고정100개 평가, ONNX 호출 경계 프로파일링, CPU·RSS·온도·클럭 계측
- **진행 방향:** Qwen 단독·1배속 연속 입력을 주 평가로 사용. ONNX INT8 2T에서 출발해 같은 파이프라인의 GGUF·실행 설정과 비교
- **현재 과제:** 추가 무음 없는100개 ONNX 연속 기준선 확보. 기존 Q8 연속 평가의 VAD 누락·분할 경계·확정 지연도 보존·분석. [기존 실측](results/summaries/optimization/realtime100-q8-20261110.md)
- **후속 구현·검증:** engine TTFT·청크 간 KV 재사용, 마이크 입력, 최종 장시간 안정성

현재는 WAV 파일로 검증합니다. 이전 Zip+VAD+Refine 자막은 `legacy/`에 보존합니다.

[최신 1배속 실측](results/summaries/optimization/realtime100-q8-20261110.md): **고정100개·16분31초 입력**을 실패·드롭 없이 완료했습니다. 연결 CER14.06%(같은 순서 기존 batch7.64%)·확정p95 6.19초로 정확도·지연 개선이 필요합니다. VAD 누락2개와 파일 경계를 넘는 최종 구간3개가 관측됐습니다. 아래 표는 기존 batch 기준값입니다.

연속 입력 중에도 추론과 다음 음성 수신이 함께 진행되어야 합니다. 새 주 기준은 **파일 사이 추가 무음0초·파일 경계 초기화 없음**이며, 기존 Q8의 추가 무음1초 결과와 구분합니다. ONNX/GGUF batch 값은 모델 자체 정확도·처리 비용을 확인하는 보조 참조입니다.

## 기존 GGUF batch 참조값

RP5 · Qwen3-ASR-0.6B Q8_0 · CPU 2스레드 · **2026-11-09 실측**입니다.

| 처리 성공 | 문자 오류율 CER ↓ | 처리 시간 비율 RTF ↓ | 관측 최대 RSS |
| ---: | ---: | ---: | ---: |
| 100/100 | 7.67% | 0.6225 | 1772.7 MiB |

입력은 Zeroth60개·FLEURS40개, 총14분51.145초이며 [출처·선정 방법](docs/research/fixed100.md)을 고정했습니다. 이후 최적화의 주 비교에는 같은 원음·순서의 연속 입력과 공통 파이프라인을 사용합니다.

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

## 핵심 코드

GGUF 실행 흐름은 **평가 실행기 → Python 어댑터 → C++ bridge → llama.cpp/libmtmd**입니다.

| 목적 | 먼저 볼 파일 |
| --- | --- |
| 모델 로딩·첫 추론·워밍업·반복 평가 | [qwen/benchmark.py](src/edge_asr/qwen/benchmark.py) |
| 1배속 연속 입력·부분 자막·실제 반환 지연 | [qwen/realtime/](src/edge_asr/qwen/realtime/) |
| GGUF 연결·음성 전달·출력 검사 | [qwen/gguf.py](src/edge_asr/qwen/gguf.py) |
| KV 초기화·오디오/prefill·토큰 생성 제어 | [native/gguf/bridge.cpp](native/gguf/bridge.cpp) |
| ONNX 참조·context 검사 / 호출 경계 계측 | [qwen/onnx.py](src/edge_asr/qwen/onnx.py) / [qwen/profiling.py](src/edge_asr/qwen/profiling.py) |
| 음성 목록·WAV 로딩 / 일반 파일 평가 | [dataset.py](src/edge_asr/dataset.py) / [evaluation.py](src/edge_asr/evaluation.py) |
| 저장 가설 재채점·CER/RTF 집계 | [scoring.py](src/edge_asr/scoring.py), [metrics.py](src/edge_asr/metrics.py) |
| CPU/RSS·native 진단 / 온도·실제 클럭 | [resources.py](src/edge_asr/resources.py), [runtime.py](src/edge_asr/runtime.py) / [thermal.py](src/edge_asr/thermal.py) |

설정은 [configs/gguf.toml](configs/gguf.toml)과 [configs/onnx.toml](configs/onnx.toml)에 있습니다. 명령 진입점은 [scripts/edge_asr.py](scripts/edge_asr.py), 상세 의존 방향은 [아키텍처](docs/reference/architecture.md)를 참고하세요.

## 폴더 구조

탐색기 기본 정렬처럼 **각 계층에서 폴더 우선·이름순**으로 표시했습니다. 자주 쓰는 하위 폴더와 핵심 파일만 펼쳤으며, 숨김 파일·생성물(`build/`, `dist/`)·캐시·과거 실험의 상세 목록은 생략했습니다. 로컬 자산 폴더는 자료가 준비된 작업 환경 기준입니다.

```text
edge-asr/
├── benchmarks/                                            # manifest 명세
├── configs/                                               # 현재 설정·이전 재현 설정
│   ├── legacy/                                            # 이전 비교·자막 설정
│   ├── quantization/                                      # text Q6·Q5·Q4 후보
│   │   ├── q4_k_m.toml
│   │   ├── q5_k_m.toml
│   │   └── q6_k.toml
│   ├── eval_manifest.example.jsonl
│   ├── gguf.toml
│   └── onnx.toml
├── data/                                                  # 로컬 음성·정답
│   ├── evaluation/
│   │   └── edge100-20261109-01/                           # 현재 고정100개
│   │       ├── metadata/
│   │       ├── original/
│   │       ├── wav/
│   │       └── manifest.jsonl
│   └── sources/                                           # 원본 코퍼스·출처 캐시
├── docs/                                                  # 프로젝트 문서
│   ├── archive/                                           # 이전 연구
│   ├── guides/                                            # 실행·데이터 안내
│   ├── reference/                                         # 구조·계측 계약
│   ├── research/                                          # 현재 연구·평가 프로토콜
│   └── status.md                                          # 현재 상태·다음 작업
├── experiments/
│   └── m1_zipformer/                                      # M1 원본 보관
├── models/                                                # 로컬 가중치
├── native/
│   ├── gguf/                                              # GGUF C ABI·빌드
│   │   ├── bridge.cpp
│   │   ├── build.py
│   │   └── quantize.py
│   └── ort_trace/                                         # ORT 호출 계측·빌드
│       ├── build.py
│       ├── ort_trace.cc
│       └── ort_trace.map
├── results/
│   ├── maintenance/                                       # 테스트·자산 보존·전송 검증
│   ├── optimization/                                      # 원시 측정값
│   │   ├── opt0/
│   │   ├── opt1/
│   │   ├── opt2/
│   │   └── opt3/
│   └── summaries/
│       └── optimization/                                  # Git에 보관하는 OPT 요약
├── runtimes/
│   └── qwen3-opt/                                         # 로컬 외부 엔진·헤더·빌드·라이브러리
├── scripts/
│   ├── benchmark_zipformer.py
│   ├── deploy_rp5.sh
│   └── edge_asr.py
├── src/
│   └── edge_asr/
│       ├── adapters/                                      # 공통 factory·이전 비교 모델
│       ├── legacy/                                        # 이전 자막·병합 재현
│       ├── qwen/                                          # 현재 Qwen 실행 경로
│       │   ├── realtime/                                  # 1배속·VAD·부분/확정 전사
│       │   │   ├── replay.py
│       │   │   ├── session.py
│       │   │   └── worker.py
│       │   ├── benchmark.py
│       │   ├── gguf.py
│       │   ├── onnx.py
│       │   └── profiling.py
│       ├── tools/
│       │   ├── data/                                      # 준비·검증·평가 순서 생성
│       │   │   ├── baseline100.py
│       │   │   ├── fixed100.py
│       │   │   └── opt0.py
│       │   ├── maintenance/
│       │   ├── probes/                                    # 실행 제한·부하 검사
│       │   │   ├── load.py
│       │   │   └── thermal.py
│       │   ├── reports/                                   # 저장 기록 분석
│       │   └── cli.py
│       ├── artifacts.py
│       ├── cli.py
│       ├── config.py
│       ├── dataset.py
│       ├── evaluation.py
│       ├── metrics.py
│       ├── resources.py
│       ├── runtime.py
│       ├── scoring.py
│       ├── thermal.py
│       └── vad_probability.py                             # 후속 연속 음성을 위한 VAD 기반
├── tests/                                                 # 공통·legacy·Qwen 회귀 검사
│   ├── common/
│   ├── legacy/
│   └── qwen/
│       ├── fixtures/
│       ├── test_baseline.py
│       ├── test_fixed100.py
│       ├── test_profiling.py
│       ├── test_qwen3.py
│       ├── test_qwen3_gguf.py
│       └── test_realtime.py
├── AGENTS.md                                              # 코드 재사용·폴더 분리·작업 원칙
├── pyproject.toml
└── README.md
```

음성·정답은 `data/`, 가중치는 `models/`, 외부 엔진·빌드는 `runtimes/`, 원시 측정·검증 로그는 `results/optimization/`·`results/maintenance/`에 보관합니다. 이 로컬 자산들은 GitHub에 포함하지 않습니다. [예시 JSONL](configs/eval_manifest.example.jsonl)은 목록 형식만 보여 주며 실제 평가 자료는 아닙니다.

## 개발·평가 흐름

**Host 개발·모의 테스트 → main commit/push → RP5 소스 동기화·실측 → 결과 회수·재채점** 순서로 진행합니다. 새 실험은 별도 설정·실행 ID를 사용하고 기존 환경·모델·원음·결과를 보존합니다. Host 테스트와 RP5 실제 추론을 구분하며, 미측정 값은 추정으로 채우지 않습니다.

코드를 추가하기 전 기존 입력·채점·계측·평가 흐름을 먼저 재사용합니다. 실험 차이는 설정으로 표현하고, 관련 기능의 책임이 커지면 역할별 하위 폴더로 분리합니다. 실험마다 전체 코드를 복제하거나 빈 폴더·추상 계층을 미리 만들지 않습니다. 이후 작업도 [코드 재사용·폴더 분리 원칙](AGENTS.md)을 따릅니다.

[전체 문서](docs/README.md) · [연구 로드맵](docs/research/qwen3_optimization.md) · [테스트 안내](tests/README.md) · [전체 OPT 기록](results/summaries/optimization/README.md) · [이전 연구](docs/archive/model-comparison/README.md) · [경로 이행표](docs/reference/structure_migration.md)
