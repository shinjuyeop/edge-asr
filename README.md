# Raspberry Pi 5 한국어 실시간 자막

Qwen3-ASR-0.6B로 Raspberry Pi 5(RP5)에서 한국어 음성을 받아 발화 중에는 부분 자막을 갱신하고 구간이 끝나면 전사를 확정합니다. 파일 전체를 빠르게 전사해도 말하는 도중 화면이 오래 멈출 수 있습니다. 이 프로젝트는 WAV를 실제 시간에 맞춰 공급하고, 문자 오류와 사람이 자막을 기다리는 시간을 함께 측정합니다. USB 마이크로도 같은 인식 경로를 사용할 수 있습니다.

## RP5와 Hailo-8L의 역할

| 단계 | T·P8 청취 구성에서 실행하는 연산 |
| --- | --- |
| 음성 전처리 | RP5 CPU가 16 kHz 모노 PCM을 받고 VAD로 발화 구간을 찾습니다. ASR에는 원음에서 계산한 log-Mel 특징을 사용합니다. |
| 음성 Encoder | Hailo-8L이 앞단 합성곱 3개와 GELU를 하나의 HEF로 실행합니다. CPU가 나머지 투영과 Transformer를 계산해 음성 임베딩을 만듭니다. |
| Prefill | CPU가 텍스트와 음성 임베딩을 처리합니다. 요청 간 동일한 텍스트 접두부의 KV 캐시를 재사용합니다. |
| Decode·표시 | CPU가 토큰을 차례로 생성하면 브라우저가 부분 자막과 확정 결과를 표시합니다. |

두 구성은 같은 GGUF Q8 모델과 CNN 가속기를 사용합니다. T는 구간을 최대 4초로 나눠 새 본문을 빨리 보여줍니다. P8은 3.4초부터 음향적 쉼을 찾고 최대 8초까지 기다려 단어 중간에서 확정하는 오류를 줄입니다. CPU만 사용하는 공유 Q8은 변경 전후를 비교하는 회귀 기준입니다. [연산 경로](docs/reference/architecture.md) · [설정 선택](configs/README.md)

## 고정 100개 원음에서의 변화

RP5에서 한국어 WAV 100개를 1배속으로 공급한 결과입니다. 지연은 서버 발행 기준 p95이며, 갱신 공백은 새 음성을 반영해 본문이 달라진 표시 사이의 시간입니다.

| 구성 | 확정 문자 오류 / 정답 3,848자 | 첫 본문 | 최신 갱신 공백 | 원음 끝 → 확정 |
| --- | ---: | ---: | ---: | ---: |
| 초기 ONNX INT8 CPU | 615 | 12.25초 | 4.60초 | 8.56초 |
| T · 반응성 우선 | 460 (11.95%) | 4.13초 | 4.00초 | 3.06초 |
| P8 · 경계 정확도 우선 | 374 (9.72%) | 9.42초 | 6.06초 | 5.42초 |

T에서 P8로 바꾸면 정답 어절 안에서 문장을 끝낸 횟수가 28회에서 10회로 줄었습니다. 초기 ONNX와는 원음과 정답이 같지만 순서·VAD·표시 방식·열 조건이 달라 누적 변화로 읽어야 합니다. [원음별 경계·지연 대조](results/summaries/optimization/boundary-20261124.md)

## 실제 음성과 자막 확인하기

1. 모델·원음·실행 기록을 준비한 장비에서 개발 PC의 저장소 루트를 엽니다. 처음 설치할 때는 [환경 준비](docs/guides/setup.md)를 따릅니다.
2. [T·P8 청취 명령](docs/guides/listener.md#음성-경계-전후-비교)으로 서버를 시작합니다. 이미 같은 기록 목록을 제공 중이면 바로 [청취 화면](http://127.0.0.1:8765)을 엽니다.
3. **기록 재생**에서 `fixed-T`와 `fixed-P8`의 같은 원음 `fleurs-1441445080190694850`를 선택해 ‘매일’에서 들리는 음성과 확정 자막을 비교합니다. **라이브**에서는 프로필과 원음을 고르고 **준비 → 시작**으로 RP5에서 새로 전사합니다. [USB 마이크 사용법](docs/guides/listener.md#rp5-usb-마이크)
4. CPU 회귀 기준을 직접 실행할 때는 모델 설정과 VAD·갱신 옵션을 함께 지정합니다. [Q8 대표 명령](docs/guides/qwen3_optimization.md#현재-개발용-대표-명령)

## 주요 경로

자주 사용하는 경로만 표시했습니다. 캐시·빌드·대용량 자산과 이전 실험의 세부 목록은 생략합니다.

```text
edge-asr/
├── benchmarks/                  입력 목록 명세
├── configs/                     모델 설정·청취 프로필
│   ├── model_comparison/        한국어 ASR 엔진 비교 설정
│   ├── quantization/            양자화 비교 설정
│   ├── gguf_realtime.toml       CPU 회귀 기준
│   ├── listener_hailo_boundary.toml
│   └── listener_hailo_continuity.toml
├── data/                        로컬 평가 음원·정답
├── docs/
│   ├── guides/                  설치·청취·동기화
│   ├── reference/               구조·환경·측정 계약
│   ├── research/                평가 설계·최적화 과정
│   └── status.md                현재 판정·다음 작업
├── experiments/                 초기 실험 재현 자료
├── models/                      로컬 가중치
├── native/
│   └── gguf/                    C++ bridge·Hailo 연결
├── results/
│   ├── optimization/            로컬 원시 실행
│   └── summaries/optimization/  공유 실험 보고서
├── runtimes/                    장비별 외부 엔진·빌드
├── scripts/
│   └── edge_asr.py              공통 CLI
├── src/edge_asr/qwen/
│   ├── realtime/                입력·요청·자막 처리
│   └── viewer/                  청취 서버·브라우저 화면
└── tests/                       Host 회귀 검사
```

## 현재 한계와 다음 방향

P8은 긴 문맥을 확보하는 동안 첫 본문이 늦어집니다. 부분 전사와 확정 전사가 CPU 워커를 공유하고, 장시간 입력에서는 열 제한이 관측됩니다. 다음 단계는 Q8 CPU와 Hailo의 배치 성능을 다시 측정하고 LiteRT-LM의 실행 가능성을 소규모로 비교하는 것입니다. 엔진 선택은 비교 후 결정합니다. [현재 상태와 우선순위](docs/status.md) · [최적화 과정](docs/research/optimization_history.md) · [문서 안내](docs/README.md) · [개발 절차](CONTRIBUTING.md)
