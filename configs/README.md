# 실행 설정

공유 개발 입구는 `gguf_realtime.toml`의 **Q8 회귀 참조**입니다. 최근 연속 수정 실험은 **Q4 속도 대조군**을 별도로 사용합니다. 두 역할은 제품 기본 채택과 구분하며, 이번 구조 정리에서 모델 기본값·채택 판정을 바꾸지 않습니다. 기존 batch 기준선과 양자화 후보도 보존합니다. 상대 자산 경로는 저장소 루트 기준이고 `asset_manifest`는 모델 폴더 기준입니다.

## 모델·평가 설정

| 파일 | 용도·채택 상태 |
| --- | --- |
| [gguf_realtime.toml](gguf_realtime.toml) | 공유 Q8 회귀 참조·생성2T/음성·prefill3T·고정 text prefix KV. 연속 옵션은 별도 지정 |
| [gguf_final_draft.toml](gguf_final_draft.toml) | 선택 Q8 확정 토큰 후보4개 묶음·같은 구간의 직전 부분 토큰만 전달. 1배속 주요 지연 개선·CER 소폭 증가, 공유 기본 유지 |
| [onnx.toml](onnx.toml) | ONNX INT8 2T 출발 참조 |
| [gguf.toml](gguf.toml) | GGUF Q8_0 2T 최적화 기준선 |
| [quantization/q6_k.toml](quantization/q6_k.toml), [q5_k_m.toml](quantization/q5_k_m.toml), [q4_k_m.toml](quantization/q4_k_m.toml) | 보존한 batch 양자화 후보 |
| [quantization/q4_k_m_realtime.toml](quantization/q4_k_m_realtime.toml) | 기존 Q4_K_M·2/3T 연속 대조 후보 |
| [quantization/q4_both_realtime.toml](quantization/q4_both_realtime.toml), [q4_prefill_realtime.toml](quantization/q4_prefill_realtime.toml) | text/audio Q4·음성/prefill 3T/4T 대조 후보 |
| [quantization/q4_0_head_realtime.toml](quantization/q4_0_head_realtime.toml) | 빠른 Q4_0 본체·출력층 / audio Q8. 최근 연속 수정 실험의 속도 대조군, Q8 공유 참조로 채택하지 않음 |
| [quantization/q4_0_partial_window.toml](quantization/q4_0_partial_window.toml) | 같은 Q4의 선택 1.99초 부분 음성 창·embedding/음성 KV 캐시. 확정은 전체 음성 경로, 기본 전환 보류 |
| [eval_manifest.example.jsonl](eval_manifest.example.jsonl) | 목록 형식 예시2줄. 실제 평가100개가 아님 |
| [legacy/](legacy/README.md) | 이전 세 모델·Zip 1배속·M1 재현 설정 |

[평가 기준](../docs/research/fixed100_baseline.md), [실측 결과](../results/summaries/optimization/fixed100-baseline-20261109.md), [실행 안내](../docs/guides/qwen3_optimization.md)를 따릅니다. 실제 음성 목록은 로컬 `data/evaluation/edge100-20261109-01/manifest.jsonl`입니다. 비교 순서는 `tools prepare-baseline100`으로 새 실행 경로에 준비합니다.

`gguf_realtime.toml`은 `results/optimization/opt3/realtime-split100-20261111-01/configs/split-2-3.toml`의 TOML 값을 그대로 공유 경로에 옮긴 개발 프로필입니다. 원본 실험 설정과 기존 `gguf.toml`·`onnx.toml`은 보존합니다. 자산이 없는 새 clone에서도 설정 검사는 가능하지만 실제 추론은 준비된 RP5 환경에서만 수행합니다.

이 파일은 모델·평가 계약을 담고 있습니다. `evaluation.mode = "batch"`를 마이크 또는 연속 입력 모드로 해석하지 않습니다. 연속 입력은 기존 `tools replay-qwen3`가 담당하며 VAD·부분 갱신·토큰 표시 옵션은 [현재 개발용 대표 명령](../docs/guides/qwen3_optimization.md#현재-개발용-대표-명령)에 함께 고정합니다. TOML을 선택하는 것만으로 이 CLI 옵션들이 활성화되지는 않습니다.

2026-11-10 경로 정리에서 기존 두 기준선은 파일명만 바꾸고 내용·SHA를 유지했습니다. 중복 OPT-0/OPT-2 설정은 통합했으며 당시 파일은 commit `9957094`에 있습니다. 후보 실험에는 새 설정과 실행 ID를 사용하고 기준 설정을 덮어쓰지 않습니다. Qwen 실행에는 설정을 명시합니다.

`quantization/q4_0_head_realtime.toml`은 2026-11-17 빠른 Q4_0 본체/출력층 후보의 동일 자산·빌드를 공유 경로에서 참조합니다. `listener_continuous.toml`은 이 단일 모델에 연속 수정 정책을 연결하는 선택 청취 프로필입니다. Q8 공유 기본과 과거 2/3회 실험 설정은 변경하지 않습니다.

## 청취 프로필

모델 TOML은 정밀도·스레드·native/캐시 자산을, listener TOML은 참조 모델·입력 목록·VAD/요청/표시 옵션을 지정합니다. 아래는 `tools listen-qwen3 --profile`에 전달하며 모델 설정의 `check-config --evaluation`과 구분합니다. `tests/qwen/test_listener.py`는 모든 공유 프로필을 기존 `replay_args()`와 replay parser로 변환하고, 참조 모델이 추적 대상 설정인지·설정 문법과 옵션이 유효한지 자산 없이 확인합니다. 모델·음원 누락은 실제 실행 준비 단계에서 별도로 검사합니다.

| 프로필 | 모델·입력·용도 |
| --- | --- |
| [listener.toml](listener.toml) | Q8 공유 회귀 참조·고정100개·첫1초/이후4초 |
| [listener_continuous.toml](listener_continuous.toml) | 빠른 Q4·고정100개·횟수 제한 없는 연속 수정 |
| [listener_continuous_paced.toml](listener_continuous_paced.toml) | 같은 Q4·실제 계산량 배분과 열 상태에 따른 갱신 감속 |
| [listener_continuous_priority.toml](listener_continuous_priority.toml) | 같은 Q4·확정 대기 시 부분 토큰 취소 |
| [listener_longform_natural.toml](listener_longform_natural.toml) | Q4 1.99초 창 캐시·자연발화8개 |
| [listener_longform_reading.toml](listener_longform_reading.toml) | Q4 1.99초 창 캐시·낭독 연결4개 |
| [listener_partial_window.toml](listener_partial_window.toml) | Q4 1.99초 창 캐시·고정100개 |
| [listener_q8_headroom.toml](listener_q8_headroom.toml) | Q8·긴 자연발화·첫 0.6초 이후 새 음성 1초부터 최신 구간 재전사. 열과 확정까지 남은 시간에 따라 후속 호출을 조절하는 실험 프로필 |
| [listener_q8_headroom_followup.toml](listener_q8_headroom_followup.toml) | Q8 확정 4개 묶음·가변 갱신. 직전 부분 문구 재생과 다음 토큰을 위한 시간이 남으면 확정 전 추가 요청을 구간당 1회 허용 |

최종 목표는 발화 중 자막이 계속 갱신되고 수정되는 스트리밍입니다. 과거 최대2회/3회 조건은 [실행 안내](../docs/guides/qwen3_optimization.md)와 실행별 listener 설정에 비교·재현 조건으로 보존합니다. 공유 프로필 간 작은 TOML 반복은 독립적인 재현 조건을 명시하기 위해 유지하며 설정 상속을 사용하지 않습니다.

`gguf_conv_realtime.toml`은 Q8 가변 갱신에서 기존 4×4 합성곱 후보만 비교하는 선택 설정입니다. 공유 Q8과의 차이는 `conv_kernel_manifest` 하나입니다. 후보 프로세스에만 명세의 `LD_PRELOAD`·`EDGE_ASR_CONV_CPU`가 필요하며 일반 listener에서 환경 없이 선택하지 않습니다. [대조 조건](../docs/research/qwen_realtime.md#q8-가변-갱신합성곱-대조--2026-11-18-사전-조건).

[긴 음원 실측](../results/summaries/optimization/q8-conv-headroom-20261118.md)에서 합성곱 비용은 줄었지만 첫 표시/첫 4자 지연이 모두 개선되지는 않아, 이 설정은 선택 후보로 유지합니다.

`quantization/q8_decoder_control.toml`은 세 decoder의 동일 빌드 대조용 Q8이며, 공유 Q8의 prefix bridge와 구분합니다. `quantization/q4_0_q6_head_realtime.toml`은 기존 Q4_0 본체/Q6_K 출력층 모델입니다. 두 설정은 현재 headroom 정책에서 출력층의 정확도·지연 영향을 확인하는 선택 평가용입니다. [조건](../docs/research/qwen_realtime.md#decoder-출력층과-가변-갱신--2026-11-18-사전-조건).

[가변 스트리밍 실측](../results/summaries/optimization/decoder-headroom-20261118.md)에서 Q6_K 출력층은 빠른 Q4 대비 CER0.41%p 개선과 지연 증가·같은 최종 실패7건을 보였습니다. 공유 기본으로 채택하지 않고 재현용 설정으로 유지합니다.

[gguf_partial_window.toml](gguf_partial_window.toml)은 Q8·기존 부분 창 bridge·0.99초 독립 창 캐시의 선택 평가 설정입니다. 현재 headroom 정책을 유지하며 같은 bridge의 창 없음/창 재계산과 구분합니다. [사전 조건](../docs/research/qwen_realtime.md#q8-부분-창과-headroom--2026-11-19-사전-조건).

[Q8 창 캐시 실측](../results/summaries/optimization/q8-cache-headroom-20261119.md)에서 최종 CER는 유지했지만 자연발화 갱신 공백과 철회가 늘어 기본으로 채택하지 않았습니다. `gguf_partial_window.toml`은 재현용 선택 설정으로 보존합니다.
