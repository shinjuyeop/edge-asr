# 현재 상태 — Qwen3-ASR 최적화

2026-11-11 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [연구 계획](research/qwen3_optimization.md), [평가 조건](research/qwen_realtime.md), [최근 분리 검토](../results/summaries/optimization/realtime-split-20261111.md).

## 유지하는 연속 입력 기준

**Qwen 단독1배속 연속 입력**이 주 평가입니다. 개발 권장은 **GGUF Q8_0·3T·VAD 보정·ASR/VAD 판정5초·첫1초/이후4초·토큰 표시/접두부 재표시 보류**입니다. 스레드 실험의 별도3T TOML을 사용하고 기존 `configs/gguf.toml`·기본2T·모델·환경·bridge는 보존합니다. 실사용 채택은 보류합니다.

같은 소스 `306f4a6`에서 기존 고유6개2T→3T→4T→2T를 선별한 뒤, 같은100개 새2T→3T를 모두 전사했습니다. Zeroth60·FLEURS40·891.1448125초·정답3848자·추가 무음0초·파일 경계 초기화 없음입니다. 스레드 수만 바꾸고 VAD0.3·rms26-floor80·문맥0·유한 큐·워밍업·온도 대기 없는 운영 정책을 유지했습니다. 추론 중에도 입력을 계속 받았습니다.

| 지표 | 새2T control | 3T |
| --- | ---: | ---: |
| 연결 CER | 11.12% —428/3848 | 동일 |
| 최종 / 부분 호출 | 210 /112 | 210 /118 |
| 호출 실패 / 입력 드롭 / 자막 없는 원음 | 0 /0 /0 | 0 /0 /0 |
| 같은210개 최종 추론 합계 | 570.26초 | **519.42초** |
| 전체 추론 합계 | 722.89초 | **663.19초** |
| 첫 표시 p50 /p95 —100개 | 3.76 /5.20초 | **3.25 /4.56초** |
| 완성 문자열 첫 반환 p95 —100개 | 5.61초 | **5.11초** |
| 파일 끝 기준 최종 반환 p95 —100개 | 4.22초 | **3.72초** |
| 관측 최대 RSS | 1831.28MiB | 1832.23MiB |
| 시작 /최고 온도 | 77.65 /85.90°C | 72.70 /87.55°C |

최종210구간 PCM·전사·토큰 ID가 같습니다. 사전100개 개발 조건13개를 모두 통과했습니다. 최종 추론 합계8.9%·전체 합계8.3%가 줄었지만 decode 자체는약11.2% 늘고 음성 처리/prefill이약22.7% 줄었습니다. 부분 호출 수가 달라 문자 철회도159→164자였으며 재표시 철회는 양쪽0입니다.

첫 표시는 원음 시작→텍스트 이벤트 발행이며 실제 화면 시각이 아닙니다. 파일 끝 반환은 원음 입력 끝→마지막으로 겹치는 최종 호출 반환이고 음향 단어 끝과 다릅니다. 원음 경계 중첩1개로 발화별 corpus CER는null이며 정답3848자 전체를 연결 CER에 포함합니다. 같은 순서 단일 운영 대조·열 제한을 포함한 값으로 일반화하지 않습니다.

## 추가 분리 실험 — 채택 보류

소스 `5dc732e`에서 선택 항목 `num_threads_batch`와 별도 bridge를 추가했습니다. 생략하면 기존5인자 ABI를 유지하고, 명시하면 생성용·음성/prefill용 스레드를 나눕니다. 같은6개로 기존 ABI 호환1회와3/3→2/3→2/3→3/3 반복을 수행했습니다.5회 모두 최종15구간 PCM·전사·토큰 ID·50/280오류 동일·실패/드롭/미출력0입니다.

두 분리 후보의 decode 합계는 앞control 대비8.69%·7.69% 줄었지만 최종 처리 합계는3.88%·2.82% 개선이었습니다. 두 반복 모두3% 이상이어야 하는 사전 조건에 미달해 **기존3T 권장 유지·신규100개 미실행**으로 판정했습니다. 위100개 수치는 이전에 검증한3T 기준이며 새 분리 후보의 성능으로 옮기지 않습니다. [조건·전체 판정](../results/summaries/optimization/realtime-split-20261111.md).

## 구현·검증

- 기존 설정·GGUF 어댑터·bridge를 확장하고 같은 실행기·채점/계측을 재사용했습니다. 선택 항목이 없으면 `num_threads`는 기존처럼 생성/prefill/오디오에 같은 값을 줍니다. [구조](reference/architecture.md), [실행 안내](guides/qwen3_optimization.md).
- 이번 Host/RP5 각각 전체378개 중377개 통과·선택 ORT 헤더 검사1개 생략. GGUF C++ 구문 검사도 양쪽에서 수행했습니다. 실제 추론은 고유6개 반복5회이며 새72파일·기존174파일·보호21항목 해시·동일 부팅·독립 재집계 일치를 확인했습니다. 기존3T100개 대조는 [이전 검증](../results/summaries/optimization/realtime-threads-20261111.md)에 보존합니다.
- engine TTFT는 native 호출 시작→첫 비EOG 토큰이며 음성 처리/prefill 포함·큐/Python 표시 제외입니다. 보존한100개 대조의 같은210개 최종 호출 p50/p95는2T 1.692/2.513초→3T 1.298/1.883초입니다. 음성 encoder 증분 실행·청크 간 KV 재사용은 미구현입니다.
- 현재100개는 낭독 개발/회귀 자료입니다. 자연 대화·잡음·독립 holdout·마이크·최종 장시간 운영·부분 정답 정확도는 미검증입니다. [데이터 출처·한계](research/fixed100.md). 이전 ONNX 재시작·화면/입력 멈춤 원인도 미확정으로 보존합니다.

## 다음 작업

1. 생성3T·음성/prefill3T와 생성2T·음성/prefill3T를 같은100개 연속 입력에서 새로 비교합니다.6개 선별 기록은 보존하고 효과 크기와 기존100개 개발 조건을 함께 판정합니다. [추가 검증 사전 조건](research/qwen_realtime.md#스레드-분리100개-추가-검증--2026-11-11-사전-조건). 현재 권장은 모두3T이며 새100개 결과는 미측정입니다. KV 재사용은 전사 동등성과 입력 변경 범위를 별도 검증합니다.
2. batch 대비 추가 오류134개와 부분 정답 정확도를 검토합니다. 자기 최종 가설 일치·문자 철회·음향 지연은 서로 구분합니다.
3. Q5/Q6/Q4는 기존 고유3개 선별 결과이며 최종 선정은 같은 연속 조건으로 판단합니다. 이후 독립 자료·마이크·장시간 운영을 검증합니다.

## 보존한 참조

[표시 안정성](../results/summaries/optimization/realtime-stability-20261111.md) · [토큰 전달](../results/summaries/optimization/realtime-token-20261111.md) · [갱신2초/4초](../results/summaries/optimization/realtime-schedule-20261111.md) · [VAD 보정](../results/summaries/optimization/realtime-accuracy-20261110.md) · [ONNX/GGUF 연속](../results/summaries/optimization/realtime100-backends-20261110.md) · [ONNX 출발·중단](../results/summaries/optimization/realtime100-onnx-20261110.md) · [batch 참조](../results/summaries/optimization/fixed100-baseline-20261109.md) · [3개 양자화](../results/summaries/optimization/realtime-controls-quant-20261110.md) · [갱신 간격 비교 영상](../results/summaries/optimization/realtime-video-20261111.md) · [전체 OPT 기록](../results/summaries/optimization/README.md).

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 현재 Qwen 단독 평가에 섞지 않습니다. [이전 연구](archive/model-comparison/README.md). 작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다.
