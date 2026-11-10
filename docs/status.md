# 현재 상태 — Qwen3-ASR 최적화

2026-11-10 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [연구 계획](research/qwen3_optimization.md), [평가 조건](research/qwen_realtime.md), [최신 비교](../results/summaries/optimization/realtime100-backends-20261110.md).

## 현재 연속 입력 기준

**Qwen 단독1배속 연속 입력**을 주 평가로 사용합니다. 기존 ONNX INT8 2T를 출발 기록으로 두고 같은 파이프라인의 GGUF Q8_0 2T와 비교했습니다. 비양자화 원본 성능이나 실사용 통과 구성을 뜻하지 않습니다.

같은 Zeroth60·FLEURS40·891.1448125초·정답3848자·추가 무음0초입니다. VAD0.3·최대5초·문맥0·부분 첫1초/이후2초·같은 유한 큐에서 추론 중에도 입력을 계속 받았습니다. 두 실행은 같은 부팅 ID와 추론 코드를 사용했고 최종214구간의 좌표·PCM 해시가 일치했습니다.

| 지표 | ONNX INT8 | GGUF Q8_0 |
| --- | ---: | ---: |
| 연결 CER | 15.98% —615/3848 | 13.23% —509/3848 |
| 호출 / 실패 / 입력 드롭 | 361 /1 /0 | 346 /0 /0 |
| 첫 자막 p50 / p95 — 같은99개 | 5.43 /12.25초 | 6.16 /10.92초 |
| 성공 구간 확정 p50 / p95 — 같은213구간 | 5.31 /7.74초 | 5.62 /7.57초 |
| 관측 최대 RSS | 2096.09MiB | 1830.53MiB |
| 최고 온도 / 현재 제한 플래그 | 85.90°C /0·6·8 | 85.90°C /0·6·8 |

GGUF는 오류106개·RSS265.56MiB를 줄이고 ONNX의1.496초 실패 구간도 성공 반환했습니다. 다만 첫 자막·확정 중앙값과 평균은 길었고 p95는 짧았습니다. **후속 최적화 주 후보는 Q8로 유지하되 모든 지연이 개선됐다고 판단하지 않습니다.** 양자화·프런트엔드·운영 열 상태를 포함한 구성 비교이며 순수 엔진 효과로 일반화하지 않습니다.

양쪽 모두 같은 원음1개가 VAD 미방출로 자막이 없고 최종6구간은 원음 경계를 넘습니다. 발화별 corpus CER는null이며 전체 연결 CER를 사용합니다. GGUF의 같은 순서 기존 batch 연결 CER7.64%보다13.23%로 악화되어 **현재 파이프라인의 실사용 채택은 보류**합니다. 개별 원인은 아직 분리하지 않았습니다.

## 구현·검증

- 기존 `qwen/realtime/` 실행기와 ONNX/GGUF 어댑터·공통 입력/채점을 재사용합니다. 각 호출은 누적 PCM의 batch 전사이며 청크 간 KV 재사용·토큰 즉시 배포는 미구현입니다. [구조](reference/architecture.md).
- 신규 추론 코드·패키지·모델 변경 없이 측정했습니다. 기존 Host/RP5 전체359개 중357개 통과·선택적 native2개 생략 기록은 [ONNX 출발 측정](../results/summaries/optimization/realtime100-onnx-20261110.md)에 있습니다. 이번 문서·CLI 검사 각6개를 통과했습니다.
- 새 GGUF 원시14개·모델/runtime/VAD 자산5개 해시가 일치하고, Host/RP5에서 각각 재집계한 비교 JSON도 바이트가 같습니다. 원시는 `results/optimization/opt3/realtime100-gguf-20261110-01/`입니다.
- 첫 ONNX 시도의 예기치 않은 재시작과 이후 화면·입력 멈춤 원인은 미확정으로 보존합니다. 새 ONNX02·GGUF 실행은 같은 부팅을 유지하며 입력을 완료했습니다. 시스템 멈춤과 모델 출력 실패를 구분합니다.
- 현재100개는 낭독 개발/회귀 자료입니다. 마이크·자연 대화·잡음·독립 holdout·최종 장시간 검증은 미완료입니다. [데이터 출처·한계](research/fixed100.md).

## 다음 작업

1. **VAD 미방출1개·경계 중첩6개와5초 분할/문맥0의 영향**을 분리합니다. 원음·정답·실패를 보존하고 같은100개로 검증합니다.
2. Q8의 부분 중복 추론·큐 대기를 줄이고 첫 자막 중앙값과 p95를 함께 관리합니다. 필요하면 prefill/decode·KV 병목을 추가 계측합니다. engine TTFT·음향 단어 시각은 미측정으로 둡니다.
3. Q5/Q6/Q4는 기존 고유3개 선별 결과입니다. 최종 양자화 선정은 같은 연속 조건의 정확도·실패·누락·지연으로 판단합니다.
4. ONNX의 긴 파일 context 거부와1.496초 출력 상한 실패, 시스템 멈춤 원인은 별도 진단으로 다룹니다. 이후 독립 자료·마이크·장시간 운영을 검증합니다.

## 보존한 참조

[ONNX 출발 실측·첫 중단](../results/summaries/optimization/realtime100-onnx-20261110.md) · [이전1초 Q8](../results/summaries/optimization/realtime100-q8-20261110.md) · [batch ONNX/GGUF](../results/summaries/optimization/fixed100-baseline-20261109.md) · [3개 양자화·부분 간격](../results/summaries/optimization/realtime-controls-quant-20261110.md) · [전체 OPT 기록](../results/summaries/optimization/README.md).

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 현재 Qwen 단독 평가에 섞지 않습니다. [이전 연구](archive/model-comparison/README.md). 작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다. [실행 안내](guides/qwen3_optimization.md).
