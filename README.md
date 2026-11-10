# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B를 Raspberry Pi 5의 ARM64 CPU에서 실행하며, **한국어 정확도와 처리 완료율을 유지하면서 인식 지연·메모리·발열을 줄이는** 프로젝트입니다.

[현재 상태·다음 작업](docs/status.md) · [연속 입력 평가 기준](docs/research/qwen_realtime.md) · [정확도 개선 결과](results/summaries/optimization/realtime-accuracy-20261110.md) · [실행 안내](docs/guides/qwen3_optimization.md)

## 현재 진행 상태

- **구현·검증:** ONNX/GGUF 단독 연속 입력100개 비교, Q8 고정 VAD 보정·구간 길이 대조, 공통 채점·자원 계측
- **진행 방향:** Qwen 단독·1배속 연속 입력을 주 평가로 사용. ONNX INT8 2T에서 출발해 같은 파이프라인의 GGUF·실행 설정과 비교
- **현재 과제:** Q8·VAD 보정·5초 구간을 개발 기준으로 첫 자막 중앙값·분할/문맥 손실·중복 추론·큐 대기 개선
- **후속 구현·검증:** engine TTFT·청크 간 KV 재사용, 마이크 입력, 최종 장시간 안정성

현재는 WAV 파일로 검증합니다. 이전 Zip+VAD+Refine 자막은 `legacy/`에 보존합니다.

## 현재 연속 입력 결과

RP5·Qwen3-ASR-0.6B GGUF Q8_0·2T·2026-11-10 실측입니다. 같은 Zeroth60개·FLEURS40개를 **14분51초·1배속·추가 무음0초** 공급하며 추론 중에도 다음 음성을 계속 받았습니다. 모델은 고정하고 VAD 입력 보정과 분할을 대조했습니다. ASR 파형은 원음을 유지합니다.

| 지표 | 기존 Q8·5초 | VAD 보정·5초 | 보정·판정30초·구간8초 |
| --- | ---: | ---: | ---: |
| 연결 CER ↓ | 13.23% | **11.12%** | 8.26% |
| 자막 없는 원음 | 1 | **0** | 0 |
| 호출 실패 / 입력 드롭 | 0 /0 | 0 /0 | 0 /0 |
| 첫 자막 p50 / p95 — 공통99개 | 6.16 /10.92초 | 6.81 /9.84초 | 8.97 /15.09초 |
| 파일 끝 기준 반환 p95 — 공통99개 | 6.59초 | 6.16초 | 10.35초 |
| 관측 최대 RSS | 1830.53MiB | 1831.22MiB | 1832.03MiB |

**다음 개발 기준은 VAD 보정만 적용한5초 구성입니다.** 오류81개·2.10%p를 줄이고 첫 자막 p95도 짧아졌지만, 첫 자막 중앙값은 약0.64초 늘었습니다.8초 복합 구성은 정확도 참고로 보존합니다. 파일 끝 기준 반환은 원음 입력 끝부터 마지막으로 겹치는 최종 호출 반환까지이며 음향 단어 끝 기준은 아닙니다. 세 구성 모두 열 제한을 관측했고 저지연 실사용 채택은 보류합니다. [조건·진단·해석](results/summaries/optimization/realtime-accuracy-20261110.md).

평가 자료는 [고정100개 출처·선정 방법](docs/research/fixed100.md)을 따릅니다. [ONNX/GGUF 비교](results/summaries/optimization/realtime100-backends-20261110.md), [ONNX 출발 기록·첫 중단](results/summaries/optimization/realtime100-onnx-20261110.md), [기존 batch 참조](results/summaries/optimization/fixed100-baseline-20261109.md), [추가 무음1초 Q8](results/summaries/optimization/realtime100-q8-20261110.md)은 보존합니다. batch RTF는 실제 자막 반환 지연을 대신하지 않습니다.

## 빠른 확인

Python3.11 이상이 필요합니다. 아래 명령은 저장소 루트의 **기존 `.venv` 환경**을 기준으로 하며 모델을 로딩하지 않습니다.

```bash
# 설정과 평가 명령 확인
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf.toml
.venv/bin/python scripts/edge_asr.py tools replay-qwen3 --help

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
| VAD 신호 보정·원음 보존 / 구간 분할 | [audio_policy.py](src/edge_asr/audio_policy.py) / [segmentation.py](src/edge_asr/segmentation.py), [vad_probability.py](src/edge_asr/vad_probability.py) |
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
│       ├── audio_policy.py
│       ├── cli.py
│       ├── config.py
│       ├── dataset.py
│       ├── evaluation.py
│       ├── metrics.py
│       ├── resources.py
│       ├── runtime.py
│       ├── scoring.py
│       ├── segmentation.py
│       ├── thermal.py
│       └── vad_probability.py                             # 연속 음성 구간 판정
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
