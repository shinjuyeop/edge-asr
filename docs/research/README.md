# 현재 연구

활성 연구는 [Qwen3-ASR 기반 ARM64 Edge 추론 최적화](qwen3_optimization.md)입니다. 단계별 계획과 판정 기준을 이 문서에서 관리하며 최신 진행 상태는 [status](../status.md)에 둡니다.

실험에서 확인한 문제와 개발 방향의 변화는 [ASR 최적화 과정](optimization_history.md)에 정리합니다. 중요한 판단이 바뀔 때 갱신하며, 상세 조건·수치는 연결된 개별 보고서를 따릅니다.

2026-11-10부터 [Qwen 단독1배속·부분 자막](qwen_realtime.md)을 우선 검증하고, 같은 연속 입력 경로에서 낮은 비트 양자화를 비교합니다. 기존 batch 기준선과 실제 자막 지연을 구분합니다.

이전 모델 비교·VAD·Zip+Refine·병합 연구 문서는 [보관 색인](../archive/model-comparison/README.md), 실측 보고서는 [결과 색인](../../results/summaries/README.md)에 있습니다. 과거 계획의 다음 작업과 신규 OPT 단계를 섞지 않습니다.

첫 실행의 고정 조건과 코드 분석은 [OPT-0 소규모 프로토콜](opt0_pilot.md)에 있습니다.

OPT-1 계측 범위와 on/off 비교는 [호출 경계 계측 계획](opt1_profiling.md)을 따릅니다.

OPT-2 첫 엔진+자산 구성 비교는 [GGUF Q8_0 프로토콜](opt2_gguf.md)에 모델·빌드·입력·판정 기준을 고정합니다.

이후 기본 입력은 [고정100개](fixed100.md)입니다. Zeroth60·FLEURS40 원본 준비·선정·Host/RP5 검증을 ASR 실측과 구분합니다.

새100개의 실제 초기값은 [ONNX 출발 기준선·GGUF 비교 프로토콜](fixed100_baseline.md)을 따릅니다. 양자화된 기존 ONNX 구성을 출발 참조로 유지하고 이후 GGUF 최적화는 같은 입력의 GGUF2T와 비교합니다.

이번 입력의 실제 초기값과 재부팅 중단·재시도 결과는 [고정100개 기준선 보고서](../../results/summaries/optimization/fixed100-baseline-20261109.md)에 분리해 기록했습니다.
