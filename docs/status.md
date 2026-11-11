# 현재 상태 — Qwen3-ASR 최적화

2026-11-11 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [연구 계획](research/qwen3_optimization.md), [평가 조건](research/qwen_realtime.md), [최신 실측](../results/summaries/optimization/realtime-stability-20261111.md).

## 현재 연속 입력 기준

**Qwen 단독1배속 연속 입력**이 주 평가입니다. 개발 기준은 **GGUF Q8_0·2T·VAD 보정·ASR/VAD 판정5초·첫1초/이후4초**입니다. 별도 bridge의 **`--token-stream`**을 선택할 수 있고, 같은 구간의 이미 보인 접두부는 새 호출이 다시 생성해도 자막을 짧게 되돌리지 않습니다. 기본 모델 설정은 보존하고 실사용 채택은 보류합니다.

최신 소스 `17446e3`에서 Zeroth60·FLEURS40·891.1448125초·정답3848자를 추가 무음0초·파일 경계 초기화 없이 입력했습니다. VAD0.3·rms26-floor80·문맥0·유한 큐·워밍업·온도 대기 없는 운영 정책을 유지했습니다. 추론 중에도 입력을 계속 받았으며 이전 최종210개 구간의 PCM·전사·토큰 ID가 같습니다.

| 지표 | 최신 RP5 100개 실행 |
| --- | ---: |
| 연결 CER | 11.12% —428/3848 |
| 최종 / 부분 호출 | 210 /115 |
| 호출 실패 / 입력 드롭 / 자막 없는 원음 | 0 /0 /0 |
| 접두부 재표시 철회 / 전체 철회 이벤트 | 0 /45회 |
| 철회된 접미부 문자 합계 | 163자 |
| 첫 텍스트 발행 p50 /p95 —원음100개 | 3.52 /4.97초 |
| 완성 문자열 첫 반환 p50 /p95 —100개 | 3.88 /5.34초 |
| 파일 끝 기준 최종 반환 p95 —100개 | 4.02초 |
| 관측 최대 RSS | 1833.70MiB |
| 시작 /최고 온도 | 73.25 /85.35°C |

위 시간은 새 실행의 관측값이며 과거와의 차이를 엔진 가속 효과로 해석하지 않습니다. 원음 입력 끝 기준과 음향 단어 끝, 작업자 발행과 실제 화면 시각을 구분합니다. 원음 경계 중첩1개로 발화별 corpus CER는null이고 정답3848자는 연결 CER에 모두 포함합니다.

## 이번 표시 개선의 효과

이전 토큰 켬100개 로그를 같은 시각으로 재생해 표시 규칙만 바꾼 **모의 분석**에서는 접두부 재표시 철회99→0회·전체 철회 문자577→161자였고 구간 첫 표시 시각·종료 result는 같았습니다. 새 RP5 실행에서도 재표시 철회0·전체163자를 확인했습니다. 실제 내용 변경·실패 처리는 허용하며 부분 호출 수도 과거113회→새115회여서 모의 효과와 새 실측을 분리합니다.

문자 철회는 `ko_cer_v1` 공통 접두부 뒤 제거 길이의 합계이며 편집 거리·부분 CER가 아닙니다. 최신 성공 부분115개 중73개가 자기 최종 가설의 접두부였습니다. 자기 최종 가설도 오류가 있어 정답 정확도 개선을 뜻하지 않습니다. [같은 로그·새 실측·한계](../results/summaries/optimization/realtime-stability-20261111.md).

## 구현·검증

- 기존 실행기·세션을 확장하고 `qwen/realtime/stability.py`의 접두부 판정·철회 집계를 재사용합니다. 모든 토큰을 보존하고 보류를 `prefix_replay_suppressed`로 기록합니다. [구조](reference/architecture.md), [실행 안내](guides/qwen3_optimization.md).
- Host/RP5 각각 전체374개 중373개 통과·선택 ORT 헤더 검사1개 생략. 실제 모델 실행은6개 파일럿과100개 재실행2건입니다. 최종 전사·4280토큰·표시 규칙·새32파일/기존62파일 해시·10자산 보존과 독립 재집계 일치를 확인했습니다. 같은 부팅 ID를 유지했습니다.
- engine TTFT는 native 호출 시작→첫 비EOG 토큰이며 음성 처리/prefill 포함·큐/Python 표시 제외입니다. 최신325호출 p50/p95는0.936/2.316초입니다. 음성 encoder 증분 실행·청크 간 KV 재사용은 미구현입니다.
- 기존 ONNX의 재시작·화면/입력 멈춤 원인은 미확정입니다. 현재100개는 낭독 개발/회귀 자료이며 자연 대화·잡음·독립 holdout·마이크·최종 장시간 운영·부분 정답 정확도는 미검증입니다. [데이터 출처·한계](research/fixed100.md).

## 다음 작업

1. 음성 처리/prefill·decode의 실행 비용을 줄일 구성을 고릅니다. 이전 기준 실행은 음성 처리/prefill426.84초·decode268.16초였습니다. 안전한 캐시 재사용은 모델 전사 동등성을 별도로 확인합니다.
2. batch 대비 추가 오류134개와 부분 가설의 정답 정확도를 검토합니다. 자기 최종 가설 일치·표시 안정성과 구분하고 실제 음향 정답 시각은 추정하지 않습니다.
3. Q5/Q6/Q4는 기존 고유3개 선별 결과이며 최종 선정은 같은 연속 조건으로 판단합니다. 이후 독립 자료·마이크·장시간 운영을 검증합니다.

## 보존한 참조

[토큰 전달 끔/켬](../results/summaries/optimization/realtime-token-20261111.md) · [갱신2초/4초](../results/summaries/optimization/realtime-schedule-20261111.md) · [VAD 보정·8초 대조](../results/summaries/optimization/realtime-accuracy-20261110.md) · [ONNX/GGUF 연속](../results/summaries/optimization/realtime100-backends-20261110.md) · [ONNX 출발·첫 중단](../results/summaries/optimization/realtime100-onnx-20261110.md) · [batch ONNX/GGUF](../results/summaries/optimization/fixed100-baseline-20261109.md) · [3개 양자화·부분 간격](../results/summaries/optimization/realtime-controls-quant-20261110.md) · [갱신 간격 비교 영상](../results/summaries/optimization/realtime-video-20261111.md) · [전체 OPT 기록](../results/summaries/optimization/README.md).

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 현재 Qwen 단독 평가에 섞지 않습니다. [이전 연구](archive/model-comparison/README.md). 작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다.
