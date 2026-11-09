# Qwen 최적화 작업 시작

현재 사용 가능한 것은 기존 sherpa-onnx INT8 어댑터, 단독 smoke·공통 batch 평가와 `tools opt0-baseline`입니다. 첫 추론·워밍업·반복·고정 PCM 진단의 조건은 [OPT-0 프로토콜](../research/opt0_pilot.md)을 따릅니다. 새 OPT 기준선 고정과 프로파일링은 [연구 계획](../research/qwen3_optimization.md)에 따라 이어서 구현합니다.

## 경로와 환경

저장소 루트에서 실행합니다. `configs/optimization/qwen3_onnx_int8.toml`은 기존 Qwen 모델·1스레드·입력/토큰 예산·3회 워밍업·`ko_cer_v1`을 유지하고 결과 상위 경로만 분리한 시작 설정입니다. 성능상 최적 스레드 수를 선언하는 설정이 아닙니다.

RP5에서 이미 검증한 `.venv-qwen3-vad-20261106-01` 또는 해당 모델 런타임이 설치된 기존 별도 Qwen 환경을 사용할 수 있습니다. 신규 엔진은 별도 환경/빌드 경로에 설치하고 기존 `.venv`와 패키지를 갱신하지 않습니다. Host 테스트에는 실제 ARM 추론이 포함되지 않습니다.

```bash
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/qwen3_onnx_int8.toml
.venv/bin/python scripts/edge_asr.py tools probe-qwen3 --help
.venv/bin/python scripts/edge_asr.py tools thermal-run --help
```

## 실제 평가 준비

평가 명령은 다음 형식입니다. 실제 manifest·고유 ID를 정한 뒤 실행하며 RP5 부하 실험은 기존 `thermal-run` 제한 아래에서 실행합니다. 아래는 호출 형식이고 완료한 OPT 실행 기록이 아닙니다.

```bash
.venv-qwen3-vad-20261106-01/bin/python scripts/edge_asr.py evaluate \
  --config configs/optimization/qwen3_onnx_int8.toml \
  --manifest <평가-manifest.jsonl> \
  --output-dir results/optimization/opt0/<고유-실행ID>/evaluations
```

`evaluate`는 상위 경로 아래 새 실행 폴더를 생성합니다. 단독 smoke의 `--output`은 새 경로여야 하며 4GiB 주소 공간 제한을 검사하므로 `thermal-run` 안에서 실행합니다. 기존 온도·시간·메모리 보호 기본값을 임의로 완화하지 않습니다.

설정/환경/모델/입력 해시와 원시 결과를 보관한 뒤 요약만 `results/summaries/optimization/`에 추가합니다. 이전 실측과 새 OPT 실행을 합쳐 성능을 주장하지 않습니다. 성공한 입력만 CER로 보고하지 말고 실패·커버리지도 표시합니다. [공통 평가·재채점](evaluation.md), [측정 계약](../reference/benchmark_design.md)을 따릅니다.
