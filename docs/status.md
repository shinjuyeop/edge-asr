# 현재 상태 — Qwen3-ASR 최적화

2026-11-09부터 프로젝트 중심을 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 최소화하는 것**으로 전환합니다. 기존 모델 비교·Zip+Refine 자막 연구는 보존하고, 신규 작업은 OPT 단계·새 실행 ID로 구분합니다.

## 현재 구현과 미완료 범위

| 영역 | 상태 |
| --- | --- |
| Qwen sherpa-onnx INT8 | RP5 로딩·전사·공통 batch 평가 완료, 어댑터 재사용 |
| Qwen llama.cpp/libmtmd Q8_0 | 별도 빌드·모델 경로, 공통 batch·반복 평가 실제 검증 완료 |
| 공통 평가 기반 | `ko_cer_v1`, 입력/모델 해시, 실패 보존, 재채점, CPU/RSS·열 계측 구현 |
| 고정100개 입력 | Zeroth60·FLEURS40, 총891.145초. Host 준비·RP5 복사/검증 완료, ASR 실측 전 |
| 이전 Zip+VAD+Refine | `legacy/`로 분리. WAV 자막 유지, 자동 교체 보류 |
| OPT-0 | 첫 소규모 단독 운영 기준선·56ms 재현 완료. 전체 평가 확장·독립 반복은 후속 |
| OPT-1 | Python·ORT 호출 경계 계측 완료. 실제 TTFT·operator별·단독 KV 비용은 미계측 |
| OPT-2 | llama.cpp/libmtmd Q8_0 첫 비교 완료. 스레드/ARM 빌드·낮은 비트 양자화 최적화는 후속 |
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

## OPT-1 첫 결과 — 2026-11-09

[기존 ONNX 호출 경계 실측](../results/summaries/optimization/opt1-profile-20261109.md)을 완료했습니다. 기존 wheel을 수정하지 않고 별도 C API 계측 라이브러리를 사용했습니다. 고정 KSS 6개에서 계측 on 12호출의 내부 전사 시간은 token decode 53.26%, prefill 18.05%, Conv 13.85%, Encoder 9.37%, 입력/특징 호출 4.41%였습니다. native decode_stream의 ONNX Run 바깥 비용은 약 1.03%이며 이를 KV 갱신만의 시간으로 해석하지 않습니다.

56ms 재현은 prefill 뒤 후속 decoder Run 64회에 약 4.6~4.7초를 썼습니다. stock 6/6·ABBA 24/24의 raw text/tokens와 진단 8개의 상태·출력은 OPT-0와 같았습니다. ABBA on/off 처리합 차이는 -0.85%로, 순수 오버헤드나 속도 개선의 추정값이 아닙니다. 첫 logits 준비·다음 decode 진입 경계를 기록했고 실제 토큰 반환 TTFT는 null입니다.

Host/RP5 각 333개 테스트(native mock 포함), 원시·빌드 37파일 해시 및 공통 재채점/분석 일치를 확인했습니다. 기존 자산은 Host 16,439개·RP5 13,973개에서 변경·누락 0입니다. 온도는 stock 최고73.25°C·ABBA81.5°C·진단80.4°C, 관측 현재 제한 플래그는 모두0입니다.

## OPT-2 첫 결과 — 2026-11-09

[GGUF Q8_0 비교](../results/summaries/optimization/opt2-gguf-20261109.md)를 완료했습니다. 공식 text/mmproj Q8_0·고정 llama.cpp를 별도 경로에서 CPU 빌드하고 기존 Python 환경의 ctypes로 연결했습니다. 두 구성 모두 KSS6개×5회 30/30 성공·CER2.20%, KSS50개 50/50 성공·정답 문자 커버리지100%입니다.

같은 시점 KSS50의 ONNX는 CER3.57%(27/756)·RTF0.7218·RSS1775.2MiB, GGUF는 CER2.65%(20/756)·RTF0.8025·RSS1673.7MiB였습니다. GGUF는 총 오류7개 감소·처리합 약11.2% 증가·RSS 약5.7% 감소이며 발화별 개선6·동률41·악화3개입니다. 사전 정확도 기준은 통과했으나 현재 구성의 속도 이득은 없었습니다. 양자화·프런트엔드도 다른 구성 전체의 운영 비교입니다.

56ms에서 ONNX는 다시 반복 경고로 실패했고 GGUF는 EOG로 종료했지만 짧은 단어를 생성했습니다. 진단 CER는 null이며 정확한 음성 보존으로 채택하지 않습니다. KSS50 최고 온도는 ONNX85.9°C·GGUF85.35°C, 두 구성 모두 현재 열 제한을 관측했습니다. 내부 첫 토큰 선택 시각은 기록했으나 사용자 TTFT·부분 자막·장시간 안정성은 아직 미계측입니다.

Host/RP5 각340개 테스트와 101개 원시/런타임/자산 파일 해시 대조·공통 재채점/분석 일치를 확인했습니다. 기존 자산 감사에서 변경·누락0입니다. 실패한 최초 smoke 준비 실행도 보존했습니다.

## 다음 작업

1. [고정100개 기준선 프로토콜](research/fixed100_baseline.md)에 ONNX 출발 기준·GGUF2T 참조, 고정 seed 순서, 실패/정확도·열/시간 조건을 고정했습니다. 새100개는 준비·전송 검증을 완료했고 실제 ASR 기준선 측정을 진행합니다. 약1,000개 확장은 선택 후속입니다. [입력 준비 기록](../results/summaries/optimization/fixed100-preparation-20261109.md).
2. 같은100개에서 ONNX/Q8_0 기준선을 확인한 뒤 Q8_0의 스레드/ARM CPU 빌드 옵션을 한 항목씩 바꾸어 측정합니다. 기존 KSS50·ONNX·Q8_0 2T 결과는 보존하고 오류·실패·문자 커버리지도 함께 비교합니다. 낭독·소규모 회귀 결과로 해석하며 자유대화·잡음·독립 holdout의 정확도로 일반화하지 않습니다. 현재 Q8_0은 속도 개선 구성으로 채택하지 않습니다.
3. 메모리 대역폭·세션 내부 KV 연산 등은 필요할 때 추가 계측합니다. 관측한 Run 바깥 1%만으로 전체 KV cache 영향이나 스레드 병목을 단정하지 않습니다.
4. 짧은 잔여 구간의 수집/문맥/확정 정책을 후보로 검증하고, 입력 누락·정답 시각·실패를 포함해 평가합니다. Qwen 단독 부분 자막·실제 TTFT는 OPT-3에서 별도로 검증합니다.

[연구 계획](research/qwen3_optimization.md), [OPT-0 고정 조건](research/opt0_pilot.md), [OPT-1 계측 정의](research/opt1_profiling.md), [OPT-2 비교 계약](research/opt2_gguf.md)을 따릅니다. Host 개발·모의 테스트 → main commit/push → RP5 동기화·실측 순서와 자산 보존 원칙을 유지합니다. 마이크 준비 전에는 WAV 입력 검증만 수행합니다.
