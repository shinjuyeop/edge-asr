# 현재 상태 — Qwen3-ASR 최적화

2026-11-11 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [연구 계획](research/qwen3_optimization.md), [평가 조건](research/qwen_realtime.md), [최근100개 분리 대조](../results/summaries/optimization/realtime-split100-20261111.md).

## 유지하는 연속 입력 기준

**Qwen 단독1배속 연속 입력**이 주 평가입니다. 다음 개발 권장은 **GGUF Q8_0·생성2T/음성·prefill3T·VAD 보정·ASR/VAD 판정5초·첫1초/이후4초·토큰 표시/접두부 재표시 보류**입니다. 별도 `results/optimization/opt3/realtime-split100-20261111-01/configs/split-2-3.toml`을 사용하며 기존 기본2T·모두3T 설정·모델·환경·bridge는 보존합니다. 실사용 채택은 보류합니다.

## 최신100개 대조

소스 `976da90`의 같은 split bridge에서 **새 생성3/음성3 → 새 생성2/음성3**을 모두 실행했습니다. Zeroth60·FLEURS40·891.1448125초·정답3848자·추가 무음0초·파일 경계 초기화 없음입니다. 생성 스레드만 바꾸고 VAD0.3·rms26-floor80·문맥0·유한 큐·워밍업·온도 대기 없는 운영 조건을 유지했습니다. 추론 중에도 입력을 계속 받았습니다.

| 지표 | 생성3 /음성·prefill3 | 생성2 /음성·prefill3 |
| --- | ---: | ---: |
| 연결 CER | 11.12% —428/3848 | 동일 |
| 최종 /부분 호출 | 210 /119 | 210 /119 |
| 호출 실패 /입력 드롭 /자막 없는 원음 | 0 /0 /0 | 0 /0 /0 |
| 같은210개 최종 추론 합계 | 514.45초 | **495.50초** |
| 전체 추론 합계 | 658.18초 | **634.32초** |
| 최종 native decode 합계 | 250.00초 | **230.59초** |
| 첫 표시 p50 /p95 —100개 | 3.15 /4.57초 | **3.06 /4.45초** |
| 완성 문자열 첫 반환 p95 —100개 | 5.07초 | **4.93초** |
| 파일 끝 기준 최종 반환 p95 —100개 | 3.69초 | **3.47초** |
| 관측 최대 RSS | 1832.81MiB | 1829.75MiB |
| 시작 /최고 온도 | 60.60 /87.00°C | 76.55 /87.00°C |

최종210구간 PCM·전사·토큰 ID는 양쪽과 이전3T100개에서 같습니다. 사전 개발 조건16개를 통과했습니다. 최종 처리 합계3.68%·decode7.76%·전체 합계3.63%가 줄었습니다. 음성 처리/prefill 합계는약0.24% 늘고4자 이상 첫 표시 p95는7.315→7.322초로 거의 같았습니다. 부분 호출119회·문자 철회170자·접두부 재표시 철회0은 양쪽 같습니다.

첫 표시는 원음 시작→텍스트 이벤트 발행이며 실제 화면 시각이 아닙니다. 파일 끝 반환은 원음 입력 끝→마지막으로 겹치는 최종 호출 반환이고 음향 단어 끝과 다릅니다. 원음 경계 중첩1개로 발화별 corpus CER는null이며 정답3848자 전체를 연결 CER에 포함합니다. 순차1쌍·서로 다른 시작 온도·양쪽 열 제한을 포함한 운영 관측으로 통계적 유의성이나 다른 환경 성능을 주장하지 않습니다.

## 이전 판정과 구현·검증

- [모두2T/3T/4T 대조](../results/summaries/optimization/realtime-threads-20261111.md)는6개 선별 뒤100개 새2T→3T를 비교해 모두3T를 권장했습니다. 당시 최종 합계8.9%·전체8.3% 감소 기록은 보존합니다.
- [스레드 분리6개 반복](../results/summaries/optimization/realtime-split-20261111.md)은 기존 ABI 호환과3/3→2/3→2/3→3/3을 확인했습니다. decode 합계는 줄었지만 최종 처리 개선3.88%·2.82%로 반복3% 선별 조건에 미달해 그 실행은100개 없이 종료했습니다. 이번100개는 별도 추가 검증이며 이전 미통과 판정은 바꾸지 않습니다.3%는 실용 기준으로 통계적 유의수준이 아닙니다.
- 선택 항목 `num_threads_batch`를 명시하면 새6인자 ABI로 생성 스레드와 음성/prefill 스레드를 나눕니다. 생략하면 기존5인자 ABI·같은 스레드 수를 유지합니다. [구조](reference/architecture.md), [실행 안내](guides/qwen3_optimization.md).
- 이번에는 모델 실행 코드 변경 없이 기존 실행기·채점·계측·분석을 재사용했습니다. Host/RP5 각각 구조·문서/CLI 검사6개·모델 없는 설정 검사2개, 새36파일·기존246파일·보호 자산23항목·동일 부팅·독립 재집계 JSON 일치를 확인했습니다. 구현 변경 당시 전체378개 중377통과·선택 ORT 헤더 검사1생략은 이전 검증이며 이번 재실행으로 세지 않습니다.
- engine TTFT는 native 호출 시작→첫 비EOG 토큰이며 음성 처리/prefill 포함·큐/Python 표시 제외입니다. 이번 최종210호출 p50/p95는3/3 1.267/1.882초→2/3 1.273/1.878초로 거의 같습니다. encoder 증분 실행·청크 간 KV 재사용은 미구현입니다.
- 현재100개는 낭독 개발/회귀 자료입니다. 자연 대화·잡음·독립 holdout·마이크·최종 장시간 운영·부분 정답 정확도는 미검증입니다. [데이터 출처·한계](research/fixed100.md). 이전 ONNX 재시작·화면/입력 멈춤 원인도 미확정으로 보존합니다.

## 다음 작업

1. 새2/3 구성을 기준으로 남은 음성 처리/prefill 비용과 첫 표시 병목을 검토합니다.2/4 같은 추가 조합이나 KV 재사용은 별도 사전 조건·새 실행으로 검증하며 효과는 아직 미측정입니다.
2. batch 대비 추가 오류134개와 부분 정답 정확도를 검토합니다. 자기 최종 가설 일치·문자 철회·음향 지연은 서로 구분합니다.
3. Q5/Q6/Q4는 기존 고유3개 선별 결과이며 최종 선정은 같은 연속 조건으로 판단합니다. 이후 독립 자료·마이크·장시간 운영을 검증합니다.

## 보존한 참조

[표시 안정성](../results/summaries/optimization/realtime-stability-20261111.md) · [토큰 전달](../results/summaries/optimization/realtime-token-20261111.md) · [갱신2초/4초](../results/summaries/optimization/realtime-schedule-20261111.md) · [VAD 보정](../results/summaries/optimization/realtime-accuracy-20261110.md) · [ONNX/GGUF 연속](../results/summaries/optimization/realtime100-backends-20261110.md) · [ONNX 출발·중단](../results/summaries/optimization/realtime100-onnx-20261110.md) · [batch 참조](../results/summaries/optimization/fixed100-baseline-20261109.md) · [3개 양자화](../results/summaries/optimization/realtime-controls-quant-20261110.md) · [갱신 간격 비교 영상](../results/summaries/optimization/realtime-video-20261111.md) · [전체 OPT 기록](../results/summaries/optimization/README.md).

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 현재 Qwen 단독 평가에 섞지 않습니다. [이전 연구](archive/model-comparison/README.md). 작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다.
