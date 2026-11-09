# 현재 상태 — Qwen3-ASR 최적화

2026-11-09부터 프로젝트 중심을 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 최소화하는 것**으로 전환합니다. 기존 모델 비교·Zip+Refine 자막 연구는 보존하고, 신규 작업은 OPT 단계·새 실행 ID로 구분합니다.

## 현재 구현과 미완료 범위

| 영역 | 상태 |
| --- | --- |
| Qwen sherpa-onnx INT8 | RP5 로딩·전사·공통 batch 평가 완료, 어댑터 재사용 |
| 공통 평가 기반 | `ko_cer_v1`, 입력/모델 해시, 실패 보존, 재채점, CPU/RSS·열 계측 구현 |
| 이전 Zip+VAD+Refine | `legacy/`로 분리. WAV 자막 유지, 자동 교체 보류 |
| OPT-0 | 첫 소규모 단독 운영 기준선·56ms 재현 완료. 전체 평가 확장·독립 반복은 후속 |
| OPT-1 | 전처리/encoder/prefill/decode 분리 프로파일링 미구현 |
| OPT-2 | llama.cpp/GGUF 어댑터·별도 빌드·동일 조건 실측 미완료 |
| OPT-3 | Qwen 단독 연속 입력·발화 중 부분 자막 미구현 |
| OPT-4 | 새 독립 평가·최종 장시간 운영 검증 미완료 |

[이번 구조 개편 검증](../results/summaries/optimization/restructure-20261109.md)은 Host/RP5 각 319개 테스트, 기존 29개 재집계 일치, 짧은 Qwen 실제 전사 회귀를 통과했습니다.

이번 폴더 개편은 연구 범위와 의존성을 정리하는 작업입니다. 최적화 효과를 측정하거나 기존 추론 정책을 바꾼 작업이 아닙니다. [구조 이행 안내](reference/structure_migration.md).

## 출발점으로 보존하는 실측

- [KSS 개발 50개](../results/summaries/qwen3-kss-dev50-20261106.md): Qwen CER 3.57%, 2T batch RTF 0.6898. 스레드별 1회 비교이므로 최적 구성 확정 근거로 삼지 않습니다.
- [동일 VAD·Zip 동시 실행](../results/summaries/qwen3-realtime-20261106.md): KSS 보정 반환 p95 3.065초. 이는 구간 종료→완성 전사 반환이며 TTFT가 아닙니다.
- [새 FLEURS·30분 검사](../results/summaries/file-validation-20261106.md): Qwen 29/30 성공, 같은 성공 29개 CER Qwen 13.19%·SenseVoice 12.93%. 실패 표본의 56ms 잔여 구간이 재현 대상입니다. 별도 30분 동시 실행 Qwen 반환 p95 3.982초·최고 87.55°C·열 제한 관측. 공통 주 프로세스 RSS 증가와 Qwen 추론 비용을 분리 조사합니다.

위 결과는 서로 다른 입력·실행 조건입니다. 새 Qwen 단독 구성의 성능으로 대체하지 않습니다. [이전 상태 전체](archive/model-comparison/status-20261106.md)와 [보고서 색인](../results/summaries/README.md)에 한계·원시 출처를 보존합니다.

## OPT-0 첫 결과 — 2026-11-09

[초기 단독 기준선·실패 재현 보고서](../results/summaries/optimization/opt0-pilot-20261109.md)를 완료했습니다. 고정 KSS 6개×5회는 30/30 성공, 기존 2T와 정규화 전사 모두 일치, 처리 RTF 0.6777·최대 RSS 1772.5MiB입니다. CER 2.20%는 이 6개(2/91자)의 값이며 기존 전체 50개 CER 3.57%의 개선을 뜻하지 않습니다.

56ms만 독립 입력하면 새 모델 첫 호출부터 65토큰 반복 경고로 약 5.2초 후 실패했습니다. 문맥을 붙이면 경고가 없어져도 원 발화 정답에 없는 출력이 있어 해결책 채택은 보류합니다. 원 발화 전체는 경고 없이 반환했습니다. encoder/prefill/decode별 시간·TTFT는 아직 null입니다.

초기 열 중단과 냉각 대기 후 사용자가 온도와 무관하게 진행하도록 요청했습니다. 최종 측정은 명시적 `operational --skip-temperature-admission`입니다. 최고 온도는 기준선 81.5°C·진단 82.05°C, 관측 현재 제한 플래그는 0입니다. 열 상태를 고정한 통제 실험과 구분하고 이후 실행도 실제 온도·클럭/감속을 결과에 기록합니다. Host/RP5 각 328개 테스트와 공통 재채점·원시 전송 대조를 통과했습니다.

## 다음 작업

1. OPT-1에서 특징·encoder·prefill·token decode 경계별 시간을 계측합니다. Python에서 가능한 범위와 native 계측이 필요한 부분을 구분합니다.
2. 같은 KSS 6개와 56ms 사례로 일반 전사와 반복 디코딩의 호출 수·생성 토큰·시간을 비교합니다.
3. 짧은 잔여 구간의 수집/문맥/확정 정책을 후보로 검증하고, 입력 누락·정답 시각·실패를 포함해 평가합니다.
4. 최적화 후보를 채택하기 전 KSS 전체 회귀와 새로운 독립 평가의 판정 기준을 확정합니다.

[연구 계획](research/qwen3_optimization.md)과 [고정 프로토콜·조건 변경](research/opt0_pilot.md)을 따릅니다. Host 개발·모의 테스트 → main commit/push → RP5 동기화·실측 순서와 자산 보존 원칙을 유지합니다. 마이크 준비 전에는 WAV 입력 검증만 수행합니다.
