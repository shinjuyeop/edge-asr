# Raspberry Pi 5 한국어 실시간 자막

Raspberry Pi 5(RP5)에서 한국어 음성을 받아 부분 자막을 갱신하고 발화가 끝나면 확정합니다. 주력 모델은 **한국어 Zipformer 174M INT8**입니다. 16kHz 모노 음성을 100ms씩 공급하고, CPU에서 320ms 증분 추론을 수행하며 encoder 상태를 이어 갑니다. WAV 1배속 입력으로 문자 오류와 자막 대기 시간을 함께 측정합니다.

## 기준선과 청취

B0는 Greedy Search, 모델 2스레드, 1.2초 trailing blank, RP5 1.5GHz CPU 상한입니다. 고정 100개에서 CER 9.80%, 원음 끝→확정 p95 1.34초를 기록했습니다. 30분 28초 입력의 CPU 점유는 18.6%, 최고 온도는 79.3°C였고 드롭·실패·확정 유실이 없었습니다. [B0 실행과 원시 기록](docs/guides/zipformer.md)

WAV 직접 재생은 `tools replay-asr`에 B0 설정을 지정합니다. 웹 청취는 B0 프로필로 RP5 서버를 준비한 뒤 포트 8765에서 원음을 선택합니다. 설치·음원·모델 준비와 SSH 터널은 실행 안내를 따릅니다. [B0 실행 명령](docs/guides/zipformer.md#대표-실행) · [환경 준비](docs/guides/setup.md) · [청취 화면](http://127.0.0.1:8765)

Qwen3-ASR T·P8과 Hailo-8L의 CNN 가속 결과는 비교 기준입니다. 모델과 NPU 배포 경로는 한국어 모델 비교 기록에서 확인할 수 있습니다. [T·P8 원음별 비교](results/summaries/optimization/boundary-20261124.md) · [한국어 ASR 모델 실측](results/summaries/asr-rp5-model-comparison-20261127.md)

## 주요 경로

자주 사용하는 경로만 표시했습니다. 캐시·빌드·대용량 자산과 이전 실험의 세부 목록은 생략합니다.

```text
edge-asr/
├── benchmarks/                  입력 목록 명세
├── configs/                     모델 설정·청취 프로필
│   ├── model_comparison/        한국어 ASR 엔진 비교 설정
│   ├── quantization/            양자화 비교 설정
│   ├── zipformer/               B0 기준선과 B1 탐색 설정
│   ├── gguf_realtime.toml       Qwen CPU 회귀 기준
│   ├── listener_hailo_boundary.toml
│   ├── listener_hailo_continuity.toml
│   └── listener_zipformer_b0.toml
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
├── src/edge_asr/
│   ├── adapters/                모델별 추론 연결
│   └── qwen/
│       ├── realtime/            입력·요청·자막 처리
│       └── viewer/              청취 서버·브라우저 화면
└── tests/                       Host 회귀 검사
```

## 현재 한계와 다음 방향

Zipformer의 저음량 인식과 첫 본문, 띄어쓰기·구두점이 개선 대상입니다. Hailo-8L은 encoder의 고정 연산부터 검토합니다. [현재 상태와 우선순위](docs/status.md) · [문서 안내](docs/README.md) · [개발 절차](CONTRIBUTING.md)
