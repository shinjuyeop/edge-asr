# 현재 상태 — Qwen3-ASR 최적화

2026-11-11 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [연구 계획](research/qwen3_optimization.md), [평가 조건](research/qwen_realtime.md), [최신 실측](../results/summaries/optimization/realtime-token-20261111.md).

## 현재 연속 입력 기준

**Qwen 단독1배속 연속 입력**을 주 평가로 사용합니다. ONNX INT8 2T 출발 기록을 보존하고 GGUF Q8_0 2T의 입력 처리·지연을 개선했습니다. 개발 기준은 **Q8·VAD 보정·ASR/VAD 판정5초·첫1초/이후4초**이며, 별도 bridge의 **`--token-stream`을 선택 가능한 개발 구성에 포함**합니다. 기본 설정은 유지하고 실사용 채택은 보류합니다.

같은 Zeroth60·FLEURS40·891.1448125초·정답3848자·추가 무음0초를 사용했습니다. VAD0.3·rms26-floor80·문맥0·같은 유한 큐에서 추론 중에도 입력을 계속 받았습니다. 양쪽 같은 새 bridge를 사용하고 생성 중 토큰 전달만 끔/켬으로 새로 전사했습니다. 최종210개 구간 좌표·PCM·전사·토큰 ID가 같습니다.

| 지표 | 토큰 전달 끔 — 새control | 켬 |
| --- | ---: | ---: |
| 연결 CER | 11.12% —428/3848 | 동일 |
| 최종 / 부분 호출 | 210 /114 | 210 /113 |
| 호출 실패 / 입력 드롭 / 자막 없는 원음 | 0 /0 /0 | 0 /0 /0 |
| 첫 표시 p50 / p95 — 공통100개 | 3.94 /5.49초 | **3.73 /5.19초** |
| 4자 이상 첫 표시 p95 — 공통100개 | 8.82초 | **7.82초** |
| 완성 문자열 첫 반환 p95 — 공통100개 | 5.49초 | 5.56초 |
| 파일 끝 기준 최종 반환 p95 — 공통100개 | 4.07초 | 4.16초 |
| 전체 추론 시간 합계 | 700.89초 | 716.28초 |
| 관측 최대 RSS | 1829.23MiB | 1832.33MiB |
| 시작 / 최고 온도 | 73.80 /84.80°C | 75.45 /85.35°C |

첫 표시는 원음 시작→UTF-8 텍스트 이벤트 발행이며 실제 화면 시각은 아닙니다. 생성 중 final도 임시 가설이고 종료 result만 확정·채점합니다. 같은 켬 실행 안에서도 완성 문자열 대비 첫 표시 p50/p95는4.15/5.56→3.73/5.19초였습니다. 전체 추론 시간은2.2% 늘어 **표시 개선과 엔진 가속을 구분**합니다. 사전 개발 조건은 모두 통과했지만 열 제한과 batch 대비 추가 오류134개가 남았습니다. 부분 가설 품질·수정 안정성도 미검증입니다.

파일 끝 기준 반환은 원음 입력 끝→마지막으로 겹치는 최종 호출 반환이며 음향 단어 끝이 아닙니다. 원음100개에 자막이 있고 최종 경계 중첩1개로 발화별 corpus CER는null입니다. 전체 정답3848자를 연결 CER에 포함합니다. 단일 순차 운영 대조로 한국어 전반의 개선이나 순수 엔진 효과로 일반화하지 않습니다.

## 구현·검증

- 기존 `qwen/realtime/`·GGUF 어댑터·C++ bridge·공통 입력/채점을 확장했습니다. 새 bridge는 별도 경로이며 기존 라이브러리·모델·환경·기본 설정을 보존합니다. [구조](reference/architecture.md), [실행 안내](guides/qwen3_optimization.md).
- **engine TTFT를 계측합니다.** native 호출 시작→첫 비EOG 토큰이며 음성 처리/prefill 포함·큐/Python 표시 제외입니다. 켬의 전체323호출 p50/p95는0.975/2.400초입니다. 음향 단어·GUI/네트워크 지연은 미측정입니다. 각 호출은 짧은 PCM의 batch 전사이며 음성 encoder 증분 실행·청크 간 KV 재사용은 미구현입니다.
- Host/RP5 각각 전체369개 중368개 통과·선택 ORT 헤더 검사1개 생략. GGUF C++ 문법 검사도 양쪽에서 통과했습니다. 모의 검사와 실제 모델 실행4건을 구분합니다.4263토큰 무결성·원시/검증/빌드62파일 SHA·기존 자산5개 보존·동일 엔진6개를 확인했고 독립 재집계 JSON도 같습니다. [검증 상세](../results/summaries/optimization/realtime-token-20261111.md).
- 첫 ONNX 시도의 예기치 않은 재시작과 화면·입력 멈춤 원인은 미확정입니다. 이번에는 같은 부팅 ID를 유지했습니다. 현재100개는 낭독 개발/회귀 자료이며 마이크·자연 대화·잡음·독립 holdout·최종 장시간 검증은 미완료입니다. [데이터 출처·한계](research/fixed100.md).

## 다음 작업

1. 생성 중 임시 자막의 정확도·수정 안정성을 확인합니다. 첫 글자·4자 이상·완성 문자열·최종 반환 지연을 계속 구분합니다.
2. 음성 처리/prefill·decode의 남은 연산 비용과 안전한 캐시 재사용을 검토합니다. 기준 실행의 음성 처리/prefill은 전체 추론 시간의60.9%였고 청크 간 KV 재사용은 미구현입니다. batch 대비 분할/문맥 오류도 함께 관리합니다.
3. Q5/Q6/Q4는 기존 고유3개 선별 결과이며 최종 선정은 같은 연속 조건으로 판단합니다. 이후 독립 자료·마이크·장시간 운영을 검증하고 ONNX 출력 실패·시스템 멈춤은 별도 진단합니다.

## 보존한 참조

[갱신2초/4초](../results/summaries/optimization/realtime-schedule-20261111.md) · [VAD 정확도 회복·8초 대조](../results/summaries/optimization/realtime-accuracy-20261110.md) · [ONNX/GGUF 연속 비교](../results/summaries/optimization/realtime100-backends-20261110.md) · [ONNX 출발·첫 중단](../results/summaries/optimization/realtime100-onnx-20261110.md) · [batch ONNX/GGUF](../results/summaries/optimization/fixed100-baseline-20261109.md) · [3개 양자화·부분 간격](../results/summaries/optimization/realtime-controls-quant-20261110.md) · [갱신 간격 비교 영상](../results/summaries/optimization/realtime-video-20261111.md) · [전체 OPT 기록](../results/summaries/optimization/README.md).

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 현재 Qwen 단독 평가에 섞지 않습니다. [이전 연구](archive/model-comparison/README.md). 작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다.
