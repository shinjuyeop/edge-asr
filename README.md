# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 RP5의 인식 지연·메모리·발열을 줄이는 프로젝트입니다. 현재는 ONNX INT8과 llama.cpp/libmtmd GGUF Q8_0의 **파일 기반 batch 평가**까지 구현했습니다. Qwen 발화 중 부분 자막과 마이크 검증은 후속 작업입니다.

## 먼저 볼 문서

평소에는 **현재 상태 → 최신 결과 → 평가 기준** 순서로 확인하면 됩니다.

| 알고 싶은 내용 | 바로가기 |
| --- | --- |
| 진행 상태와 다음 작업 | [현재 상태·다음 작업](docs/status.md) |
| 현재 비교 기준값·실패·온도 기록 | [고정100개 ONNX/GGUF 실측](results/summaries/optimization/fixed100-baseline-20261109.md) |
| 평가 조건과 개선 판정 기준 | [고정100개 기준선 프로토콜](docs/research/fixed100_baseline.md) |
| 전체 연구 방향과 OPT-0~4 단계 | [Qwen 최적화 연구 계획](docs/research/qwen3_optimization.md) |
| 음성100개의 출처와 선정 방법 | [데이터 출처·선정·검증](docs/research/fixed100.md) |
| 실제 명령과 실행 환경 | [Qwen 실행 안내](docs/guides/qwen3_optimization.md) |

최근 GGUF 2T는100/100 성공·CER7.67%·RTF0.6225입니다. ONNX 출발 참조는99/100 성공이며 기존 context 검사 거부1건이 있습니다. 같은 성공99개의 CER는 ONNX9.61%·GGUF7.53%입니다. 이번 GGUF 자체2T 값을 이후 최적화 기준으로 사용합니다. 온도·재부팅 조건과 해석 범위는 위 실측 보고서에서 확인하세요.

병목을 자세히 볼 때는 [ONNX 호출 경계 프로파일](results/summaries/optimization/opt1-profile-20261109.md), 정의를 확인할 때는 [CER·RTF·자원·지연 측정 계약](docs/reference/benchmark_design.md)을 봅니다. 나머지 문서는 [문서 색인](docs/README.md), 과거 OPT 결과는 [최적화 결과 색인](results/summaries/optimization/README.md)에서 찾습니다.

## 주로 읽고 수정할 코드

실행 경로는 **CLI → 평가 실행기 → 모델 어댑터 → 저장 결과·채점**입니다. GGUF 내부 추론을 최적화할 때는 어댑터에서 C++ bridge로 따라갑니다.

| 역할 | 파일 | 볼 내용 |
| --- | --- | --- |
| 명령 진입점 | [scripts/edge_asr.py](scripts/edge_asr.py), [cli.py](src/edge_asr/cli.py), [tools/cli.py](src/edge_asr/tools/cli.py) | 명령 인자·실행할 도구 선택 |
| 고정 기준선 실행 | [tools/optimization/baseline.py](src/edge_asr/tools/optimization/baseline.py) | 모델1회 로딩·첫 추론·워밍업·반복·실패 기록. `benchmark-qwen3`가 호출 |
| **GGUF Python 연결** | [adapters/qwen3_gguf.py](src/edge_asr/adapters/qwen3_gguf.py) | 모델·런타임 해시, ctypes C ABI, PCM 전달·결과 검사 |
| **GGUF 실제 추론** | [gguf/bridge.cpp](experiments/qwen3_optimization/gguf/bridge.cpp), [빌드 안내](experiments/qwen3_optimization/gguf/README.md) | llama.cpp/libmtmd 로딩·KV 초기화·오디오/prefill·토큰 decode |
| ONNX 출발 참조 | [adapters/qwen3.py](src/edge_asr/adapters/qwen3.py) | sherpa-onnx 호출·context 예산·출력/경고 검사 |
| 공통 평가·채점 | [evaluation.py](src/edge_asr/evaluation.py), [scoring.py](src/edge_asr/scoring.py), [metrics.py](src/edge_asr/metrics.py) | 일반 파일 평가·저장 가설 재채점·`ko_cer_v1`·CER/RTF 집계 |
| 입력·자원·열 | [dataset.py](src/edge_asr/dataset.py), [resources.py](src/edge_asr/resources.py), [열 실행기](src/edge_asr/tools/probes/thermal.py), [센서](src/edge_asr/thermal.py) | WAV/해시 검증·CPU/RSS·온도/실제 클럭·실행 제한 |
| 회귀 검사 | [GGUF 테스트](tests/optimization/test_qwen3_gguf.py), [실행기 테스트](tests/optimization/test_opt0_baseline.py), [전체 테스트 안내](tests/README.md) | Host 모의 검증과 실제 RP5 추론의 구분 |

공통 코드의 책임과 의존 방향은 [아키텍처](docs/reference/architecture.md)에 있습니다.

## 지금 사용하는 설정과 입력

| 항목 | 위치 |
| --- | --- |
| ONNX INT8 2T 기준 설정 | [fixed100_onnx_2t.toml](configs/optimization/fixed100_onnx_2t.toml) |
| GGUF Q8_0 2T 기준 설정 | [fixed100_gguf_q8_2t.toml](configs/optimization/fixed100_gguf_q8_2t.toml) |
| 실제 평가100개 | 로컬 `data/evaluation/edge100-20261109-01/manifest.jsonl` — Zeroth60·FLEURS40, 총14분51.145초 |
| 평가 순서 준비 | [prepare_baseline100.py](experiments/qwen3_optimization/prepare_baseline100.py) — 원본 목록을 보존하고 새 실행 경로에 고정 seed 순서 생성 |
| 데이터 준비·재검증 | [prepare_fixed100.py](experiments/qwen3_optimization/prepare_fixed100.py), [준비 절차](docs/research/fixed100.md) |
| manifest 형식 예시 | [eval_manifest.example.jsonl](configs/eval_manifest.example.jsonl), [필드 명세](benchmarks/manifest.md) — 예시2줄이며 실제 평가 데이터가 아님 |

음원·가중치·원시 결과는 로컬 자산이며 GitHub에는 올라가지 않습니다. 이전1T·KSS 실험 설정은 [설정 색인](configs/README.md)에서 구분합니다.

## 저장소 구조

```text
edge-asr/
├── README.md                          # 현재 작업의 문서·코드 안내판
├── src/edge_asr/
│   ├── adapters/                      # Qwen ONNX·GGUF와 비교 모델 연결
│   ├── tools/optimization/            # 기준선·재현·프로파일 실행
│   ├── tools/probes/                  # 공통 열·시간·메모리 제한
│   ├── evaluation.py, scoring.py, ... # 공통 입력·평가·채점·계측
│   └── legacy/                        # 이전 Zip+VAD+Refine·병합·자막
├── configs/optimization/              # 현재 Qwen 설정
├── experiments/qwen3_optimization/    # C++ bridge·프로파일러·입력 준비
├── docs/{research,guides,reference}/  # 계획·사용법·구조/계측 계약
├── results/
│   ├── summaries/optimization/        # Git에 보관하는 실측 요약
│   ├── optimization/                  # 로컬 원시 측정값
│   └── maintenance/                   # 로컬 테스트·보존·전송 검증 기록
├── tests/{common,optimization,legacy}/
├── scripts/                          # CLI 진입점·배포 도구
├── benchmarks/                       # manifest 명세·초기 프로브 안내
└── data/, models/, runtimes/          # 로컬 음성·가중치·외부 엔진/빌드
```

자주 수정할 곳은 `src/edge_asr/`, `configs/optimization/`, `experiments/qwen3_optimization/`입니다. 실험 뒤에는 `results/summaries/optimization/`에 요약을 추가하고 `docs/status.md`를 갱신합니다. 폴더별 안내는 [data](data/README.md)·[models](models/README.md)·[runtimes](runtimes/README.md)·[results](results/README.md)에 있습니다.

## 빠른 확인

저장소 루트의 기존 환경에서 실행합니다. 아래 명령은 실제 모델을 로딩하지 않습니다.

```bash
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/fixed100_onnx_2t.toml
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/fixed100_gguf_q8_2t.toml
.venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

작업 순서는 **Host 개발·테스트 → main commit/push → RP5 소스 동기화·실측**입니다. 실제 추론 명령·별도 실행 환경·새 실행 ID는 [실행 안내](docs/guides/qwen3_optimization.md)를 따릅니다.

<details>
<summary>이전 연구·자막·구조 이행 자료</summary>

- [이전 모델 비교·자막 연구](docs/archive/model-comparison/README.md), [기존 실측 기록](results/summaries/README.md)
- [WAV 자막 사용법](docs/guides/legacy/live_captions.md), [legacy 설정](configs/legacy/README.md)
- [구조 이행 기록](docs/reference/structure_migration.md), [M1 원본 실험](experiments/m1_zipformer/README.md)

`src/edge_asr/legacy/`, `configs/legacy/`, `docs/archive/`, `experiments/m1_zipformer/`는 이전 실험 재현용입니다. `live`는 기존 Zip+Refine 자막이고, 설정을 생략한 `evaluate`의 기본 모델은 Zipformer입니다. Qwen 작업은 위 설정을 명시합니다.

</details>
