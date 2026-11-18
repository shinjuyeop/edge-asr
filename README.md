# Qwen3-ASR 기반 ARM64 Edge 추론 최적화

Qwen3-ASR-0.6B를 Raspberry Pi 5의 ARM64 CPU에서 실행하며 **한국어 정확도·처리 완료율을 유지하면서 인식 지연·메모리·발열을 줄이는** 프로젝트입니다.

[음성·자막 청취](docs/guides/listener.md) · [개발 안내](CONTRIBUTING.md) · [현재 환경](docs/reference/environment.md) · [현재 상태·다음 작업](docs/status.md) · [RP5 실행 안내](docs/guides/qwen3_optimization.md)

WAV 1배속 입력과 **RP5 USB 마이크**로 부분·확정 자막을 확인합니다. [측정 웹의 마이크 탭](docs/guides/listener.md#rp5-usb-마이크)에서 준비·시작·입력 종료·녹음 재생을 지원합니다. 개발에는 [공유 설정](configs/gguf_realtime.toml)과 VAD·갱신·토큰 표시 옵션을 포함한 [대표 명령](docs/guides/qwen3_optimization.md#현재-개발용-대표-명령)을 사용합니다. 서비스 자동 실행·마이크 정답 정확도·최종 장시간 안정성은 후속 검증입니다.

공유 개발 입구 `gguf_realtime.toml`은 **Q8 회귀 참조**이며, 최근 연속 수정 실험의 **Q4 속도 대조군**과 구분합니다. 목표는 발화 중 자막이 계속 갱신·수정되는 스트리밍입니다. 과거 최대2회/3회는 비교·재현 조건으로 보존합니다. [모델 설정·listener 프로필 색인](configs/README.md)에서 역할과 채택 상태를 확인하세요.

## 주요 결과

Q8 가변 갱신에 기존 4×4 합성곱만 적용해 같은 긴 음원 12개를 1배속 비교했습니다. 최종 233구간 PCM·전사·토큰, CER 12.03%, 실패·드롭 0을 유지했습니다. 최신 음성 반영 공백 p95는 자연발화 4.30→4.19초·낭독 4.72→4.61초, 확정 p95는 3.74→3.70초·4.11→4.01초였습니다. 총 추론 감소는 0.86%였고 자연발화 첫 4자 p95가 7.13→8.42초로 늘어 선택 후보로 보존하며 공유 기본은 유지합니다. [전체 결과](results/summaries/optimization/q8-conv-headroom-20261118.md).

- **자막 누락:** 낮은 신호의 VAD 입력을 보정하고 ASR에는 원음을 유지해, 고정 100개 평가에서 자막 없는 원음을 없앴습니다.
- **자막 대기:** 부분 재전사 주기를 조정해 큐 대기를 줄이고, 생성 중 토큰 표시와 접두부 재표시 보류를 추가했습니다.
- **반복 연산:** 생성·음성 처리 스레드를 분리하고, 호출 사이에 음성 앞 고정 text prefix KV만 재사용했습니다.

대표 대조는 **2026-11-12, RP5·같은 빌드·낭독 WAV 100개·1배속 입력·워밍업 후**의 고정 9토큰 KV 재사용 끔/켬 비교입니다.

| 지표 | 끔 | 켬 |
| --- | ---: | ---: |
| 전체 추론 합계 — 부분 재전사 포함 | 623.42초 | **566.28초 — 9.16% 감소** |
| 첫 자막 이벤트 발행 p95 | 4.41초 | **4.13초** |
| 연결 CER | 11.12% | 11.12% |

최종 210구간의 PCM·전사·토큰 ID는 같고 실패·입력 드롭·자막 없는 원음은 양쪽 모두 0입니다. 단일 순차 대조로 시작 온도·열 상태·부분 호출 수가 달라 전체 개선을 캐시만의 효과로 단정하지 않습니다. 부분 자막 철회량은 늘었으며 첫 표시는 실제 GUI 시각과 구분합니다. [대조 조건·전체 결과·한계](results/summaries/optimization/realtime-prefix-cache-20261112.md).

후속 [음성 단계 비용 추정100개 대조](results/summaries/optimization/realtime-cost-20261113.md)는 고정 대비 갱신 공백 p50을4.64→4.14초,5초 초과 공백 원음을34→10개로 줄이고 CER11.12%·완료율을 유지했습니다. 추론11.80%·문자 철회182→267자·최종 반환 p95 2.78→2.97초도 늘어, 기본 고정을 유지하며 청취 비교 후보로 제공합니다.

최신 [첫 요청0.6초·100개 확인](results/summaries/optimization/realtime-first-request100-20261116.md)은 같은 Q8 가변 조건에서 첫 표시 p95를4.16→3.77초로 줄이고 최종 전사·CER11.12%를 유지했습니다. 첫4자 p95는6.90→7.17초로 늘어 기본 전환을 보류하고, 짧은 초기 결과 뒤의 후속 요청 예산을 다음 검토 대상으로 둡니다.

[거부된 후속 요청의 비용 진단](results/summaries/optimization/blocked-followup-cost-20261116.md)은 RP5 고정 PCM 40호출로 비용 추정의 보수성과 큐 대기 문제를 구분했습니다. 정책·공유 기본은 유지하며 연속 입력의 지연 개선은 후속 비교에서 확인합니다.

후속 [음성 비용 축소·문맥 24개 비교](results/summaries/optimization/realtime-scaled-cost-20261116.md)는 최종 전사·CER 10.20%를 유지했지만 지연 개선 조건에 미달해 기본 전환을 하지 않았습니다. 부분 목록의 VAD 절단 위치가 원래 100개와 달라진 재현 한계도 함께 기록했습니다.

[원래 100개 경계·ASR 5초/3초 대조](results/summaries/optimization/realtime-queue-20261116.md)는 첫 4자 p95를 7.16→4.98초, 최종 반환 p95를 3.09→1.95초로 줄였습니다. 다만 CER 11.12→12.81% 악화와 짧은 잔여 요청 실패 2회가 있어 3초를 기본으로 채택하지 않았습니다.

최신 [확정 5초 유지·부분 예산 비교](results/summaries/optimization/realtime-partial-budget-20261116.md)는 최종 210구간 전사·CER 11.12%·실패/드롭 0을 유지했습니다. 부분 계산 예산 3.0→3.2초만으로는 첫 4자 p95가 7.14→7.17초, 갱신 공백 p95가 4.87→4.84초여서 개선 조건에 미달했습니다. **빠른 임시 표시와 5초 확정 정확도 유지**를 주력으로 두고 예산 3.0초를 유지합니다.

[Q8 재프로파일링](results/summaries/optimization/reprofile-q8-20261116.md)은 최신 850호출 재집계와 RP5 추가 48호출로 병목을 좁혔습니다. 전체 호출 비용은 decode 47%·encoder 29%·음성 prefill 16%였고, 긴 첫 4자 지연은 후속 요청 없이 확정까지 기다리는 경로도 컸습니다.

[프로파일링 그래프 3장](results/summaries/optimization/profile-visuals-20261117.md)에서 **단계별 추론 시간·첫 자막 대기 경로·encoder/decode 내부 연산**을 볼 수 있습니다. 11월 16일 저장된 측정값을 그린 자료이며 합성곱 최적화 이후의 새 계측은 아닙니다.

[전처리·추론·후처리 흐름](docs/reference/architecture.md#q8-전처리추론후처리)에는 VAD 보정, 음성 특징 추출, 텍스트 토큰화, 글자 복원과 자막 표시를 구분했습니다. 현재 encoder 시간에는 DFT/Mel 전처리도 포함됩니다.

후속 [짧은 첫 결과 보호·100개 비교](results/summaries/optimization/realtime-short-followup-20261116.md)는 최종 전사·CER 11.12%·실패/드롭 0을 유지했지만 첫 4자 p95 6.60→6.70초·공백 p95 4.83→5.17초로 개선되지 않아 보호 기본은 끔으로 유지했습니다.

최신 [Q8 GEMV 연산 선별](results/summaries/optimization/decode-q8-20261116.md)은 합성 72조건의 비트 일치·실제 36호출의 전사/token 동등성을 확인했습니다. 정수 누적을 분리해도 두 교차 구간의 decode 감소가 0.001%·0.414%여서 적용하지 않았습니다.

후속 [Q8 CPU·캐시 진단](results/summaries/optimization/pmu-q8-20261117.md)은 실제36호출 전사/token을 유지하고 작업자 계산량 약50:50·backend stall/cycle 약81%를 확인했습니다. 메모리 대역폭 병목을 확정한 것은 아니며 가중치 사전 읽기(prefetch)를 후속 후보로 정했습니다.

[Q8 prefetch](results/summaries/optimization/prefetch-q8-20261117.md)와 [FP16 내적 결합](results/summaries/optimization/encoder-f16-20261117.md)은 선별 기준에 미달해 기본에 적용하지 않았습니다. 최신 [FP16 합성곱4×4 타일](results/summaries/optimization/conv-tile-20261117.md)은 원래 무계측 실행 대비 **encoder13.2~13.3%·전체4.0~4.5% 감소**와 전사/token 동등성을 확인해 후속 후보로 채택했습니다. 후속 [같은100개1배속 앞뒤 대조](results/summaries/optimization/conv-realtime-20261117.md)도 통과했습니다. 최종 전사·CER11.12%를 유지하며 확정 추론2.7~4.1% 감소, 첫4자 p95 **6.95~7.06→6.61초**, 갱신 공백 **5.16~5.17→4.84초**를 확인했습니다. 첫 자막 p95 개선은17~77ms로 작았고 열 제한을 포함한 순차 대조입니다. 선택 후보로 보존하며 공유 기본은 유지합니다.

최신 [OpenMP 대기 정책](results/summaries/optimization/omp-wait-20261117.md)은 일관된 개선이 없어 미지정을 유지했습니다. 후속 [Q4_0·출력층·KleidiAI 비교](results/summaries/optimization/decoder-q4-20261117.md)는 실제 토큰 간격을 Q8 약63~64ms에서40ms/34ms로 줄였습니다. 그러나 같은6개에서 CER17.86→20.71/21.07%·빈 부분 전사 실패각1건으로 채택하지 않았습니다. 두 Q4 후보의 선택은 KleidiAI OFF·생성2T였으며 공유 Q8·2/3T를 유지합니다.

후속 [Q4 부분 요청3회 비교](results/summaries/optimization/realtime-q4-followup-20261117.md)는 같은 최종 전사·CER에서 갱신 공백 p95를4.61~4.63→3.59초로 줄였고 계산량은20~21% 늘었습니다. 청취 후보로 보존합니다. 기록 화면은 **한 원음 재생→확정 전사 확인·자동 정지→다음 음원**을 지원하며 원래 연속 재생도 선택할 수 있습니다.

최신 [연속 수정 자막·100개 대조](results/summaries/optimization/continuous-q4-20261117.md)는 횟수 제한 없는 최신 입력 재전사·수정/삭제·최종 우선 취소·열 backoff를 구현했습니다. 같은100개 최종 PCM/전사/token·CER12.42%는 유지했으나 열 backoff 상태622초·최종 반환 p95 2.02→2.64초여서 기본 채택을 보류합니다. 선택 청취 프로필을 제공하고, 작은 Mel 캐시는 반복 계산을 줄였지만 전체 지연 이득이 없어 비활성 실험으로 남깁니다.

후속 [고정 냉각·연속 갱신 조절](results/summaries/optimization/continuous-paced-20261117.md)은 점진적 열 제어·실제 계산량 배분·확정 예약/취소 유예를 구현했습니다. 같은20분 최종299구간·CER21.4464%를 유지하고 추론9.51%를 줄였으나, Q4 3회 대비 첫 표시 p95 3.46→4.22초·최종 반환1.45→1.88초로 늘었습니다. 선택 청취 후보로 제공하며 기본 전환은 보류합니다.

최신 [부분 음성 창·KV 캐시](results/summaries/optimization/audio-window-cache-20261118.md)를 선택 경로로 구현했습니다. 같은100개에서 최종210구간·CER12.42%를 유지하며 갱신 공백 중앙값은3.42→2.61초로 줄었지만, 최종 반환 p95는1.83→2.34초·추론 합계는29.6% 늘었습니다. 기존 기본은 유지하고 **실험 Q4 · 1.99초 음성 창 캐시**를 청취 후보로 제공합니다.

후속 [약 1분 긴 음원 12개 대조](results/summaries/optimization/longform-stream-20261118.md)는 워밍업의 전체 파일 입력 오류를 수정하고 자연발화8개·낭독 연결4개를 각각 비교했습니다. 최종 233구간·합산 CER 15.73%는 같았고 낭독의 파일별 갱신 공백은 줄었습니다. 그러나 ASR 구간 확정 p95는 두 종류 모두 약 0.7초·총 추론은 21.2% 늘어 기본 전환을 보류합니다. 청취 화면의 108기록/32프로필에서 긴 음원도 확인할 수 있습니다.

문제 발견과 선택의 흐름은 [ASR 최적화 과정](docs/research/optimization_history.md), 현재 채택한 구성과 최신 결과는 [현재 상태](docs/status.md)에 정리했습니다. [자막 비교 영상](results/summaries/optimization/realtime-video-20261111.md)은 저장된 로그의 동기 재생 자료입니다.

## 처음 시작하기

Python 3.11 이상을 사용합니다. 현재는 **Docker 없이 장비별 Python 가상환경과 native 라이브러리**를 사용합니다. 새 작업 환경은 [설치 안내](docs/guides/setup.md), 기존 환경의 버전·용도는 [환경 기록](docs/reference/environment.md)을 확인하세요.

아래는 준비된 Host `.venv`에서 실행하는 모델 없는 검사입니다.

```bash
.venv/bin/python scripts/edge_asr.py tools list
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf_realtime.toml
.venv/bin/python scripts/edge_asr.py tools replay-qwen3 --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

실제 추론에는 모델·음원·RP5용 native 빌드가 별도로 필요합니다. Git clone에는 이 자산이 포함되지 않습니다. [실제 평가 순서](docs/guides/qwen3_optimization.md#실제-평가-순서)를 따르며 기존 환경·모델·원음·결과는 보존합니다.

고정100개를 보완하는 [약1분 긴 발화 세트](docs/research/longform.md)는 자연발화8개·낭독 연결4개, 총약12분13초입니다. 별도 입력 목록으로 준비했으며 배포 전사 기반 개발용입니다. 독립 청취 검수는 아직 하지 않았으며, RP5 성능은 [긴 음원 대조](results/summaries/optimization/longform-stream-20261118.md)에서 확인했습니다.

## 어디를 수정하나요?

실행 흐름은 **CLI → Qwen 실행기 → ONNX/GGUF 어댑터 → native 런타임**입니다. 입력·채점·계측·기록은 공통 모듈을 재사용합니다.

| 작업 | 먼저 볼 위치 |
| --- | --- |
| 음성·자막 직접 확인 | [청취 도구 사용법](docs/guides/listener.md), [viewer/](src/edge_asr/qwen/viewer/) |
| 연속 입력·부분/확정 자막 | [qwen/realtime/](src/edge_asr/qwen/realtime/) |
| 모델 로딩·반복 평가 | [qwen/benchmark.py](src/edge_asr/qwen/benchmark.py) |
| 모델 호출·출력 검사 | [qwen/gguf.py](src/edge_asr/qwen/gguf.py), [qwen/onnx.py](src/edge_asr/qwen/onnx.py) |
| C++ 연결·빌드 | [native/gguf/](native/gguf/), [native 안내](native/README.md) |
| 입력·채점·기록·계측 | [아키텍처의 공통 모듈 표](docs/reference/architecture.md) |
| 데이터 준비·진단·보고서 | [tools/](src/edge_asr/tools/), [명령 안내](docs/reference/tooling.md) |
| 변경 검증 | [tests/](tests/README.md), [개발 절차](CONTRIBUTING.md) |

`tools list`는 현재 Qwen·공통 도구를, `tools list --group all`은 과거·유지보수 도구까지 표시합니다. `live`와 `repeat-zipformer`는 과거 재현용입니다. 일반 `evaluate` 명령은 `--config`로 모델을 명시합니다.

## 폴더 구조

<details>
<summary>주요 경로 펼치기</summary>

각 계층에서 **폴더 우선·이름순**으로 정렬했습니다. 자주 쓰는 경로만 펼쳤으며 캐시·생성물(`build/`, `dist/`)·대용량 자산·과거 실험의 상세 목록·그 밖의 숨김 파일은 생략했습니다. 자산 폴더는 준비된 로컬 환경 기준입니다.

```text
edge-asr/
├── .github/
│   └── workflows/
│       └── host.yml                     # 모델 없는 Host CI
├── benchmarks/                          # manifest 명세·이전 M1 결과 경로
├── configs/
│   ├── legacy/                          # 이전 모델 비교 설정
│   ├── quantization/                    # Q6·Q5·Q4 후보
│   │   ├── q4_0_head_realtime.toml     # 빠른 Q4_0 본체·출력층, audio Q8
│   │   ├── q4_0_partial_window.toml    # 선택 1.99초 부분 음성 창 캐시
│   │   ├── q4_both_realtime.toml        # text Q4_K_M + audio Q4_0 비교
│   │   ├── q4_k_m_realtime.toml         # 현재 bridge·2/3·prefix KV의 Q4 비교
│   │   └── q4_prefill_realtime.toml     # Q4 음성·prefill4T 선별 후보
│   ├── eval_manifest.example.jsonl
│   ├── gguf.toml                        # 보존한 Q8 2T 기준선
│   ├── gguf_conv_realtime.toml          # Q8 가변·선택 4×4 합성곱 대조
│   ├── gguf_realtime.toml               # 공유 Q8 회귀 참조
│   ├── listener.toml                    # 청취용 모델·음원·연속 옵션
│   ├── listener_continuous.toml         # 최신 음성으로 연속 수정
│   ├── listener_continuous_paced.toml    # 고정 냉각·지속 부하 조절
│   ├── listener_continuous_priority.toml # 최종 대기 시 선택 부분 취소
│   ├── listener_longform_natural.toml   # 창 캐시·자연발화8개
│   ├── listener_longform_reading.toml   # 창 캐시·낭독 연결4개
│   ├── listener_partial_window.toml     # 창 캐시·고정100개
│   └── onnx.toml                        # ONNX 출발 참조
├── data/                                # 로컬 음원·정답
│   ├── evaluation/
│   │   ├── edge100-20261109-01/          # 고정100개·manifest.jsonl
│   │   └── longform-20261117-01/         # 약1분 자연발화8개·낭독 연결4개
│   └── sources/                         # 원본 코퍼스·출처 캐시
├── docs/
│   ├── archive/                         # 이전 연구·이행 기록
│   ├── guides/                          # 설치·평가·RP5 동기화
│   ├── reference/
│   │   ├── architecture.md
│   │   ├── environment.md               # 확인한 Host/RP5 환경
│   │   └── tooling.md
│   ├── research/                        # 현재 연구·평가 프로토콜
│   │   ├── optimization_history.md      # 문제 발견·선택·검증의 흐름
│   │   ├── qwen3_optimization.md         # 연구 계획
│   │   └── qwen_realtime.md              # 연속 입력 평가 조건
│   └── status.md                        # 현재 기준·판정·다음 작업
├── experiments/
│   └── m1_zipformer/                    # 초기 원본 보존
├── models/                              # 로컬 가중치
├── native/
│   ├── gguf/                            # bridge·KV/연산·커널 계측·빌드·양자화
│   │   ├── conv_sgemm.cpp               # 선택 FP16 합성곱 타일 대조
│   │   ├── conv_sgemm.patch             # 고정 upstream 사본의 최소 변경
│   │   ├── f16_dot.cpp                  # 선택 FP16 변환·내적 결합 실험
│   │   ├── kernel_trace.cpp             # 선택 커널 시간·작업자·PMU 진단
│   │   ├── perf_counters.h              # 작업자별 Linux PMU 그룹
│   │   └── q8_gemv.cpp                  # 선택 Q8 GEMV 누적·prefetch 실험
│   └── ort_trace/                       # ORT 호출 계측·빌드
├── results/
│   ├── maintenance/                     # 로컬 검사·환경 스냅샷
│   ├── optimization/                    # 로컬 원시 측정·영상
│   └── summaries/
│       └── optimization/                # Git에 공유하는 측정 요약
├── runtimes/
│   └── qwen3-opt/                       # 로컬 외부 엔진·빌드·라이브러리
├── scripts/
│   ├── benchmark_zipformer.py           # 과거 M1 호환 진입점
│   ├── deploy_rp5.sh                    # 과거 rsync 재현용
│   └── edge_asr.py                      # 체크아웃의 공통 CLI
├── src/
│   └── edge_asr/
│       ├── adapters/                    # 공통 factory·이전 비교 모델
│       ├── legacy/
│       │   ├── realtime/                # 이전 자막 실행
│       │   ├── tools/                   # 이전 비교·병합 도구
│       │   └── benchmark_zipformer.py
│       ├── qwen/
│       │   ├── realtime/
│       │   │   ├── continuous.py         # 최신 범위·완료 알림·열 backoff
│       │   │   ├── replay.py
│       │   │   ├── session.py
│       │   │   ├── stability.py
│       │   │   └── worker.py
│       │   ├── viewer/                  # 라이브·기록 청취 도구
│       │   │   ├── web/                  # 공통 음성·자막·지표 화면
│       │   │   │   ├── app.js            # 브라우저 시작 진입점
│       │   │   │   ├── captions.mjs      # 연속 수정 자막·revision 보호
│       │   │   │   ├── listener.mjs       # 재생·결과 대기, 직접 import 가능한 화면 제어
│       │   │   │   └── review.mjs        # 원음별 재생 경계·확정 결과 확인
│       │   │   ├── analysis.py
│       │   │   ├── runs.py
│       │   │   └── server.py
│       │   ├── audio_windows.py         # 선택 부분 음성 창의 요청별 ABI 수명
│       │   ├── benchmark.py
│       │   ├── convolution.py            # 선택 합성곱 후보 검증·호출 기록
│       │   ├── feature_cache.py          # 선택 정규화 전 log-Mel 접두 캐시
│       │   ├── gguf.py
│       │   ├── onnx.py
│       │   └── profiling.py
│       ├── tools/
│       │   ├── data/
│       │   │   ├── external.py          # 공통 archive·PCM 변환·출처 영수증
│       │   │   ├── fixed100.py          # 고정100개 선정·준비·재검증
│       │   │   ├── longform.py          # 긴 발화 선정·연결·재검증
│       │   │   └── zeroth.py            # 공유 Zeroth 원본·전사·후보 처리
│       │   ├── maintenance/
│       │   ├── probes/
│       │   ├── reports/
│       │   └── cli.py
│       ├── artifacts.py                 # 결과 기록·해시
│       ├── cli.py
│       ├── config.py
│       ├── dataset.py                   # 입력 목록·PCM
│       ├── evaluation.py
│       ├── metrics.py
│       ├── microphone.py                # ALSA 수집·유한 큐·녹음 보존
│       ├── resources.py
│       ├── runtime.py
│       ├── scoring.py
│       ├── segmentation.py
│       └── streaming.py
├── tests/
│   ├── common/
│   ├── legacy/
│   └── qwen/
├── AGENTS.md                            # 구현·보존 원칙
├── CONTRIBUTING.md                      # 환경 준비·검증·실행 절차
├── pyproject.toml
└── README.md
```

</details>

## 개발과 기록

**Host 개발·검사 → main 반영·push → RP5 동기화·필요한 실제 추론 → 결과 회수** 순서입니다. 개발·검증 기준은 [CONTRIBUTING](CONTRIBUTING.md), 소스 전달은 [RP5 동기화 안내](docs/guides/deployment.md)를 따릅니다.

README는 프로젝트 소개와 시작 경로, `docs/research/optimization_history.md`는 판단이 바뀐 과정, `docs/status.md`는 현재 판단과 다음 작업, `results/summaries/`는 실측 근거를 담당합니다. 설정·구조 변경은 관련 문서와 함께 갱신하며, 과거 실행 기록은 그대로 보존합니다.

[전체 문서](docs/README.md) · [평가 조건](docs/research/qwen_realtime.md) · [전체 측정 요약](results/summaries/optimization/README.md) · [경로 이행표](docs/reference/structure_migration.md)

선택 부분 음성 창 실험은 `native/gguf/audio_window_cache.h`·`src/edge_asr/qwen/audio_windows.py`에 있으며 기존 bridge/worker를 확장합니다. [동작·설정](docs/guides/qwen3_optimization.md#부분-음성-창-캐시-실험)을 참고하세요.
