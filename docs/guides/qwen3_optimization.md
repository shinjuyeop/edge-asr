# Qwen 최적화 실행 안내

현재 기준은 [고정100개 ONNX/GGUF 실측](../../results/summaries/optimization/fixed100-baseline-20261109.md)입니다. ONNX INT8 2T는 프로젝트 출발 참조이고 이후 GGUF 변경은 이번 GGUF Q8_0 2T 결과와 비교합니다. 조건·실패 처리·온도 정책·재시도 범위는 [기준선 프로토콜](../research/fixed100_baseline.md)에 있습니다.

## 환경과 설정

저장소 루트에서 기존 환경을 사용합니다.

| 실행 위치·역할 | Python | 설정 |
| --- | --- | --- |
| Host 설정 검사·모의 테스트 | `.venv/bin/python` | 아래 두 설정 모두 검사 가능. ARM 실제 추론은 RP5에서 수행 |
| RP5 ONNX 실제 추론 | `.venv-qwen3-onnx-20261106-01/bin/python` | [fixed100_onnx_2t.toml](../../configs/optimization/fixed100_onnx_2t.toml) |
| RP5 GGUF 실제 추론 | `.venv/bin/python` | [fixed100_gguf_q8_2t.toml](../../configs/optimization/fixed100_gguf_q8_2t.toml) |

GGUF는 기존 Python의 ctypes로 별도 llama.cpp/libmtmd C ABI를 호출합니다. 소스·빌드·바이너리는 `runtimes/qwen3-opt/llama-q8-20261109-01/`, 가중치는 `models/qwen3-opt/gguf-q8-20261109-01/`에 있습니다. [연결·빌드 안내](../../experiments/qwen3_optimization/gguf/README.md)를 따르며 기존 환경에 패키지를 갱신하지 않습니다.

아래 명령은 Host에서도 모델 로딩 없이 확인할 수 있습니다.

```bash
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/fixed100_onnx_2t.toml
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/fixed100_gguf_q8_2t.toml
.venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 --help
.venv/bin/python scripts/edge_asr.py tools thermal-run --help
```

## 실제 평가 순서

1. Host에서 변경·[모의 테스트](../../tests/README.md)를 마치고 main에 commit/push합니다. RP5는 같은 소스를 pull합니다.
2. [고정100개](../research/fixed100.md)의 원본·WAV·해시를 확인합니다. [prepare_baseline100.py](../../experiments/qwen3_optimization/prepare_baseline100.py)로 원본을 보존한 채 **새 실행 경로**에 고정 순서 manifest를 준비합니다. 비교 구성의 순서·워밍업 입력은 같습니다.
3. RP5에서 다른 실제 추론과 겹치지 않게 실행합니다. `tools benchmark-qwen3 --repetitions 1`은 모델1회 로딩 → 첫 추론1회 → 추가 워밍업3회 →100개 각1회이며 `thermal-run`의 외부 시간·4GiB 주소 공간 제한 안에서 사용합니다.
4. 원시·해시를 Host에 회수하고 공통 재채점·실패/커버리지·열 상태를 검토한 뒤 [요약 폴더](../../results/summaries/optimization/README.md)와 [현재 상태](../status.md)를 갱신합니다.

다음은 **GGUF 운영 측정의 호출 형식**입니다. `<...>`를 준비한 동일 순서 manifest와 새 실행 경로로 바꿉니다. 기존 사용자가 허용한 온도 대기 없는 운영 조건을 명시하며 온도·실제 클럭·제한 플래그를 기록합니다. 통제 비교의53°C 시작·75°C 경고/80°C 중단 조건과 전체 비교 순서는 기준선 프로토콜에서 구분합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "results/optimization/opt2/<고유ID>/gguf-2t" \
  --timeout-s 1800 --temperature-policy operational --skip-temperature-admission \
  -- .venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 \
  --config configs/optimization/fixed100_gguf_q8_2t.toml \
  --manifest "<동일순서-manifest.jsonl>" \
  --output "results/optimization/opt2/<고유ID>/gguf-2t" --repetitions 1
```

ONNX 측정은 guard와 자식 명령 모두 표의 ONNX Python·설정을 사용합니다. `evaluate`도 공통 batch 평가를 지원하지만 첫 추론/워밍업을 분리하는 이번 기준선은 `benchmark-qwen3`를 사용합니다. [공통 평가·재채점](evaluation.md), [계측 범위](../reference/benchmark_design.md)를 따릅니다.

## 이전 실험 재현

이전1T 시작 설정과 KSS 설정은 [설정 색인](../../configs/README.md)에 보존합니다. 소규모 반복·56ms 진단은 [OPT-0](../research/opt0_pilot.md), 별도 C API/ORT 호출 계측은 [OPT-1](../research/opt1_profiling.md), 최초 GGUF/KSS 비교는 [OPT-2](../research/opt2_gguf.md)를 따릅니다. `opt0-baseline`은 현재 `benchmark-qwen3`와 같은 실행기 이름입니다. 프로파일 계측 실행과 일반 성능 측정은 구분합니다.
