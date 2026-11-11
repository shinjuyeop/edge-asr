# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B를 Raspberry Pi 5의 ARM64 CPU에서 실행하며, **한국어 정확도와 처리 완료율을 유지하면서 인식 지연·메모리·발열을 줄이는** 프로젝트입니다.

[현재 상태·다음 작업](docs/status.md) · [연속 입력 평가 기준](docs/research/qwen_realtime.md) · [최근 스레드 분리 검토](results/summaries/optimization/realtime-split-20261111.md) · [갱신 간격 비교 영상](results/summaries/optimization/realtime-video-20261111.md) · [실행 안내](docs/guides/qwen3_optimization.md)

## 현재 진행 상태

- **구현·검증:** ONNX/GGUF·VAD 보정·부분 갱신 간격·생성 중 표시·접두부 재표시 보류·스레드를100개 단독 연속 입력에서 검증, 공통 채점·TTFT·자원 계측
- **진행 방향:** Qwen 단독·1배속 연속 입력을 주 평가로 사용. ONNX INT8 2T에서 출발해 같은 파이프라인의 GGUF·실행 설정과 비교
- **현재 과제:** Q8·권장3T·VAD 보정·구간5초·첫1초/이후4초·토큰 표시를 유지하며 남은 연산 지연·분할/문맥 손실 개선
- **후속 구현·검증:** 음성 처리/prefill·decode 최적화와 안전한 캐시 재사용, 마이크 입력, 최종 장시간 안정성

현재는 WAV 파일로 검증합니다. 이전 Zip+VAD+Refine 자막은 `legacy/`에 보존합니다.

## 연속 입력 대조 결과

RP5·Qwen3-ASR-0.6B GGUF Q8_0·2026-11-11 실측입니다. 같은 Zeroth60개·FLEURS40개를 **14분51초·1배속·추가 무음0초** 공급하며 추론 중에도 다음 음성을 받았습니다. 같은 모델·bridge·VAD 보정·5초·첫1초/이후4초·토큰 표시를 유지하고 스레드 수만 바꿨습니다.6개2T→3T→4T→2T 선별 후,100개 새2T→3T를 비교했습니다.

| 지표 | 새2T control | 3T |
| --- | ---: | ---: |
| 연결 CER | 11.12% —428/3848 | **동일** |
| 자막 없는 원음 / 호출 실패 / 입력 드롭 | 0 /0 /0 | 0 /0 /0 |
| 같은210개 최종 추론 합계 | 570.26초 | **519.42초** |
| 전체 추론 합계 —부분 재전사 포함 | 722.89초 | **663.19초** |
| 첫 표시 p50 /p95 —100개 | 3.76 /5.20초 | **3.25 /4.56초** |
| 파일 끝 기준 최종 반환 p95 —100개 | 4.22초 | **3.72초** |
| 관측 최대 RSS | 1831.28MiB | 1832.23MiB |
| 최고 온도 | 85.90°C | 87.55°C |

**다음 개발 권장은 Q8·3T입니다.** 최종210구간 PCM·전사·토큰 ID를 유지하면서 최종 호출 합계8.9%·전체 추론 합계8.3%가 줄었습니다. 음성 처리/prefill은 빨라지고 decode는 느려지는 절충이 있으며 양쪽 열 제한도 관측했습니다. 기존2T 기본 설정은 보존하고3T는 별도 TOML로 지정합니다. 단일 운영 대조이며 저지연 실사용 채택은 보류합니다. [조건·비용·개발 설정](results/summaries/optimization/realtime-threads-20261111.md).

추가로 **생성2T·음성/prefill3T** 분리 기능을 구현하고 같은6개를 반복했습니다. decode 합계는 줄었지만 최종 처리 합계 개선이3.88%·2.82%여서 두 반복 모두3% 이상이라는 사전 조건에 미달했습니다. 신규100개는 실행하지 않고 위의 기존3T 권장을 유지합니다. [분리 구현·반복 판정](results/summaries/optimization/realtime-split-20261111.md).

첫 표시는 원음 시작→텍스트 이벤트 발행이며 실제 화면 시각은 아닙니다. 파일 끝 기준 반환은 원음 입력 끝→마지막으로 겹치는 최종 호출 반환이고 음향 단어 끝과 다릅니다. 생성 중 자막은 임시 가설이며 접두부 재표시 철회는 양쪽0입니다. [표시 안정성 검증](results/summaries/optimization/realtime-stability-20261111.md).

평가 자료는 [고정100개 출처·선정 방법](docs/research/fixed100.md)을 따릅니다. [토큰 전달 끔/켬](results/summaries/optimization/realtime-token-20261111.md), [갱신2초/4초](results/summaries/optimization/realtime-schedule-20261111.md), [VAD 보정·8초 대조](results/summaries/optimization/realtime-accuracy-20261110.md), [ONNX/GGUF 연속 비교](results/summaries/optimization/realtime100-backends-20261110.md), [ONNX 출발·첫 중단](results/summaries/optimization/realtime100-onnx-20261110.md), [batch 참조](results/summaries/optimization/fixed100-baseline-20261109.md)는 보존합니다. batch RTF는 자막 반환 지연을 대신하지 않습니다.

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
| 1배속 연속 입력·부분/생성 중 자막·실제 반환 지연 | [qwen/realtime/](src/edge_asr/qwen/realtime/) |
| 자막 접두부 재표시 판정·문자 철회·최종 가설 일치 | [qwen/realtime/stability.py](src/edge_asr/qwen/realtime/stability.py) |
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
│       │   │   ├── stability.py
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
│       ├── test_caption_stability.py
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
