# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

**Low-Latency Korean ASR on Raspberry Pi 5**

Qwen3-ASR-0.6B를 Raspberry Pi 5에서 실행하며 **한국어 정확도를 유지하면서 실제 음성 인식 지연을 줄이는** 프로젝트입니다. 추론 시간뿐 아니라 실패·메모리·발열·연속 입력의 안정성을 함께 평가합니다. Host에서 개발·모의 테스트한 뒤 main에 반영하고 RP5에서 실제 추론을 검증합니다.

현재 sherpa-onnx INT8과 별도 llama.cpp/libmtmd Q8_0 batch 어댑터·공통 평가기가 구현되어 있습니다. [새 고정100개 기준선](results/summaries/optimization/fixed100-baseline-20261109.md)에서 ONNX는99/100 성공(기존 context 검사 거부1개), GGUF는 재부팅 후 새 전체 실행에서100/100 성공·CER **7.67%**·RTF **0.6225**였습니다. ONNX 성공99개 CER9.61%를 전체100개 정확도로 채택하지 않습니다. 이후 GGUF 최적화는 이번 GGUF2T 자체 기준선과 비교합니다. 온도·부팅 조건이 다른 운영 관측이며 실제 TTFT·부분 자막·장시간 안정성은 후속입니다. 이전 모델 비교와 Zip+Refine 자막은 `legacy`로 보존합니다.

- [현재 상태와 다음 작업](docs/status.md)
- [OPT-0~4 연구 계획](docs/research/qwen3_optimization.md)
- [구조·경로 이행 안내](docs/reference/structure_migration.md)
- [이전 모델 비교·자막 연구](docs/archive/model-comparison/README.md)
- [전체 실측 기록](results/summaries/README.md)

## 저장소 구조

```text
src/edge_asr/
├── adapters/                 # Qwen ONNX·GGUF와 비교 기준 모델 어댑터
├── evaluation.py, scoring.py # 공통 평가·정규화·재채점
├── streaming.py, vad*.py     # 재사용 가능한 입력·VAD·계측 기반
├── tools/
│   ├── optimization/        # Qwen smoke·ONNX/GGUF 반복·재현·ORT 계측
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

기존 `results/` 실행 ID·요약 파일, `.venv*`, `models/`, `data/`, M1 원본은 같은 위치에 보존합니다. 외부 엔진 소스·빌드는 `runtimes/`에서 별도로 관리합니다.

## 시작하기

저장소 루트에서 기존 환경을 사용합니다. 아래 설정 검사는 모델을 로딩하지 않습니다.

```bash
.venv/bin/python scripts/edge_asr.py env
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/optimization/qwen3_onnx_int8.toml
.venv/bin/python scripts/edge_asr.py tools list
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

실제 평가는 [Qwen 최적화 실행 안내](docs/guides/qwen3_optimization.md)를 따릅니다. 일반 시작 설정과 별개로 고정100개 기준선은 [사전 고정 프로토콜](docs/research/fixed100_baseline.md)의 두2T 설정을 사용합니다. 마이크는 아직 없고 파일 기반으로 검증합니다.

[고정100개](docs/research/fixed100.md) 준비와 Host/RP5 검증을 완료했습니다. Zeroth60개·FLEURS40개, 총 **14분51.145초**, 4.2~23.04초의 낭독 음성입니다. 입력 목록은 `data/evaluation/edge100-20261109-01/manifest.jsonl`입니다. 기존 자료와 PCM·정규화 정답 중복0이며 실제 ASR 결과는 [별도 기준선 보고서](results/summaries/optimization/fixed100-baseline-20261109.md)에 기록했습니다. 개발·후보 비교에 같은100개를 사용하고 약1,000개 확장은 선택 후속으로 둡니다. [선정·원본 검증·한계](results/summaries/optimization/fixed100-preparation-20261109.md)를 확인하세요.

기존 `evaluate`, `score`, `live`, `tools` 명령 이름은 유지합니다. 설정 파일 위치는 `configs/legacy/`로 바뀌었고, 인자 없는 `evaluate`의 기본 모델은 기존 Zipformer입니다. 과거 자막은 [legacy 사용법](docs/guides/legacy/live_captions.md)을 참고하세요. 문서 전체는 [문서 색인](docs/README.md), 계측 정의는 [측정 계약](docs/reference/benchmark_design.md)에 있습니다.
