# 현재 상태 — Qwen3-ASR 최적화

2026-11-10 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [연구 계획·OPT-0~4](research/qwen3_optimization.md).

## 현재 구현

| 영역 | 상태 |
| --- | --- |
| ONNX INT8 / GGUF Q8_0 | RP5 CPU 어댑터·단독 batch 평가 구현 |
| 공통 평가 | 해시·실패 보존·`ko_cer_v1`·재채점·CPU/RSS·온도/실제 클럭 계측 |
| 입력 | 고정 Zeroth60·FLEURS40, 총891.1448125초·정답3,848자. [출처·한계](research/fixed100.md) |
| 구조 | Qwen Python은 `qwen/`, C++는 `native/`, 설정은 `configs/{onnx,gguf}.toml`로 통합. [검증 기록](../results/summaries/optimization/layout-cleanup-20261110.md) |
| 미완료 | Qwen 발화 중 부분 자막·사용자 TTFT·독립 holdout·최종 장시간 검증. 마이크 없음 |

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 자동 교체는 보류 상태입니다. [이전 연구](archive/model-comparison/README.md).

## 지금 사용하는 기준선

[고정100개 프로토콜](research/fixed100_baseline.md)과 [실측 보고서](../results/summaries/optimization/fixed100-baseline-20261109.md)를 따릅니다. 출발 참조는 기존 ONNX INT8 2T이며 FP16 원본 모델을 뜻하지 않습니다. 이후 GGUF 변경은 이번 GGUF2T 자체 값과 비교합니다.

| 구성 | 성공 | 성공 파일 CER | 성공 파일 RTF | 관측 최대 RSS |
| --- | ---: | ---: | ---: | ---: |
| ONNX INT8 2T | 99/100 | 9.61% (361/3,758) | 0.7483 | 2570.4MiB |
| GGUF Q8_0 2T | 100/100 | 7.67% (295/3,848) | 0.6225 | 1772.7MiB |

ONNX의23.04초1개는 보수적 context 검사에서 거부됐습니다. 전체100개 주 CER/RTF는 null이고 실패를 빈 가설로 둔 보조 CER는11.72%입니다. 같은 성공99개에서 GGUF CER는7.53%, 처리 합은 ONNX보다 약16.8% 작았습니다. 이번 구성의 실사용 관측값이며 엔진 외 양자화·프런트엔드·재부팅 상태도 달라 재현성 확인이 필요합니다.

ONNX 최고87.55°C·실제1.5~2.4GHz·열 제한, 재부팅 후 GGUF 최고75.45°C·약2.4GHz·현재 제한0을 기록했습니다. 첫 GGUF 미완료 원시는 보존하고 새 실행에서 전체100개를 처음부터 평가했습니다. 네트워크 단절 중 로컬 기록은 계속됐으나 이전 부팅 로그가 없어 단절 원인은 미확정입니다.

이번 구조 정리는 Host/RP5 각347개 테스트, 이전 공통 집계50개, 실제 회귀9건을 통과했습니다. **기준선 수치를 새로 측정한 작업은 아닙니다.**

## 다음 작업

1. ONNX의 실제 prompt/context 예산을 검증하고 긴 파일 거부를 별도 변경으로 다룹니다. 기존 입력·실패 기록은 보존합니다.
2. 같은100개와 GGUF2T 기준으로 스레드·ARM CPU 빌드 등을 한 항목씩 바꿉니다. 추가 오류·실패·빈 출력 없이 처리 비용이 줄었는지와 열 상태·재현성을 확인합니다.
3. 필요하면 KV·메모리 병목을 추가 계측합니다. 관측한 ONNX Run 바깥1%만으로 전체 KV 비용을 단정하지 않습니다.
4. Qwen 단독 VAD·연속 입력·부분 결과와 실제 TTFT·백로그·드롭은 OPT-3에서 검증합니다. 네트워크 장애는 모델 실패와 구분해 추적합니다.

## 이전 단계의 근거

[OPT-0 기준선·56ms 재현](../results/summaries/optimization/opt0-pilot-20261109.md) · [OPT-1 호출 경계 프로파일](../results/summaries/optimization/opt1-profile-20261109.md) · [OPT-2 최초 GGUF/KSS 비교](../results/summaries/optimization/opt2-gguf-20261109.md) · [전체 OPT 기록](../results/summaries/optimization/README.md). 각 당시 데이터·조건·한계는 해당 보고서에서 확인합니다.

작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다. [실행 안내](guides/qwen3_optimization.md), [코드 구조](reference/architecture.md).
