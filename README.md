# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B를 Raspberry Pi 5에서 실행하며 **한국어 정확도를 유지하면서 실제 음성 인식 지연을 줄이는** 프로젝트입니다. 추론 시간뿐 아니라 실패·메모리·발열·연속 입력의 안정성을 함께 평가합니다. Host에서 개발·모의 테스트한 뒤 main에 반영하고 RP5에서 실제 추론을 검증합니다.

현재 Qwen의 sherpa-onnx INT8 batch 어댑터와 공통 평가기가 구현되어 있습니다. 첫 OPT-0 소규모 운영 기준선과 56ms 오류 재현을 완료했습니다. 구간별 프로파일링, GGUF 엔진 비교, Qwen 단독 부분 자막은 후속 작업입니다. [첫 결과](results/summaries/optimization/opt0-pilot-20261109.md). 이전 Zipformer/SenseVoice/Qwen 비교와 Zip+Refine 자막은 `legacy`로 구분해 보존합니다. 이전 실측을 새 최적화 성과로 표시하지 않습니다.

- [현재 상태와 다음 작업](docs/status.md)
- [OPT-0~4 연구 계획](docs/research/qwen3_optimization.md)
- [구조·경로 이행 안내](docs/reference/structure_migration.md)
- [이전 모델 비교·자막 연구](docs/archive/model-comparison/README.md)
- [전체 실측 기록](results/summaries/README.md)

## 저장소 구조

```text
src/edge_asr/
├── adapters/                 # Qwen ONNX와 비교 기준 모델 어댑터
├── evaluation.py, scoring.py # 공통 평가·정규화·재채점
├── streaming.py, vad*.py     # 재사용 가능한 입력·VAD·계측 기반
├── tools/
│   ├── optimization/        # Qwen smoke·반복 기준선·고정 PCM 재현
│   ├── data/                # 공통 평가 데이터 준비
│   ├── probes/, reports/    # 공통 부하·열 검사와 냉각 보고
│   └── maintenance/         # 자산 보존·유지보수
└── legacy/
    ├── realtime/            # 기존 Zip+VAD+Refine·WAV 자막
    ├── tools/               # 이전 모델 비교·VAD·병합 실행/집계
    └── *.py                 # 병합·토큰 정렬 실험
configs/{optimization,legacy}/
experiments/{qwen3_optimization,m1_zipformer}/
docs/{research,reference,guides,archive}/
tests/{common,optimization,legacy}/
results/optimization/        # 새 OPT 원시, Git 제외
results/summaries/optimization/ # 새 OPT 요약
```

기존 `results/` 실행 ID·요약 파일, `.venv*`, `models/`, `data/`, M1 원본은 같은 위치에 보존합니다. 향후 외부 엔진 소스·빌드는 `runtimes/`에서 별도로 관리합니다.

## 시작하기

저장소 루트에서 기존 환경을 사용합니다. 아래 설정 검사는 모델을 로딩하지 않습니다.

```bash
.venv/bin/python scripts/edge_asr.py env
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/qwen3_onnx_int8.toml
.venv/bin/python scripts/edge_asr.py tools list
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

실제 평가는 [Qwen 최적화 실행 안내](docs/guides/qwen3_optimization.md)를 따릅니다. 새 설정은 기존 Qwen 모델·디코딩 정책을 재사용하는 시작점이며 반복 실측으로 확정한 OPT 기준선은 아닙니다. 마이크는 아직 없고 파일 기반으로 검증합니다.

기존 `evaluate`, `score`, `live`, `tools` 명령 이름은 유지합니다. 설정 파일 위치는 `configs/legacy/`로 바뀌었고, 인자 없는 `evaluate`의 기본 모델은 기존 Zipformer입니다. 과거 자막은 [legacy 사용법](docs/guides/legacy/live_captions.md)을 참고하세요. 문서 전체는 [문서 색인](docs/README.md), 계측 정의는 [측정 계약](docs/reference/benchmark_design.md)에 있습니다.
