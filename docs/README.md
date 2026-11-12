# 문서 안내

[개발 안내](../CONTRIBUTING.md)에서 시작하고 [현재 상태](status.md)로 작업 기준을 확인합니다.

| 목적 | 문서 |
| --- | --- |
| 시작·개발 환경 | [개발 안내](../CONTRIBUTING.md), [확인한 Host/RP5 환경](reference/environment.md), [설치](guides/setup.md) |
| 새 프로젝트 방향·단계 | [OPT-0~4](research/qwen3_optimization.md), [현재 연구 색인](research/README.md) |
| 문제 발견·선택의 흐름 | [ASR 최적화 과정](research/optimization_history.md) |
| 음성·자막 청취 | [라이브·기록 청취 도구](guides/listener.md) |
| 실행 준비 | [Qwen 최적화 시작](guides/qwen3_optimization.md), [설치](guides/setup.md), [배포](guides/deployment.md) |
| 현재 평가 조건·결과 | [현재 상태](status.md), [고정 text prefix KV 100개 대조 — 2026-11-12](../results/summaries/optimization/realtime-prefix-cache-20261112.md), [batch 출발 기준선](research/fixed100_baseline.md) |
| 현재 우선 검증 | [Qwen 단독1배속·부분 자막·양자화 계획](research/qwen_realtime.md) |
| 평가 기반 | [고정100개 입력](research/fixed100.md), [데이터](guides/datasets.md), [공통 평가](guides/evaluation.md), [manifest](../benchmarks/manifest.md) |
| 코드·경로 | [아키텍처](reference/architecture.md), [이번 구조 이행](reference/structure_migration.md), [도구](reference/tooling.md), [테스트](../tests/README.md) |
| 계측 정의 | [측정 계약](reference/benchmark_design.md) |
| 새 OPT 요약 | [optimization 기록](../results/summaries/optimization/README.md) |
| 이전 연구·실측 | [모델 비교 보관 문서](archive/model-comparison/README.md), [기존 결과](../results/summaries/README.md) |
| 이전 자막 사용 | [legacy WAV 자막](guides/legacy/live_captions.md), [Zip 1배속 평가](guides/legacy/streaming_evaluation.md) |
| 더 이전 이행 기록 | [초기 검사](archive/verification.md), [폴더 통합](archive/consolidation.md), [옛 명령](archive/command_migration.md) |

사용법은 `guides/`, 코드/계측 계약은 `reference/`, 활성 계획은 `research/`, 이전 계획은 `archive/`, 측정값과 검증 결과는 `results/summaries/`에 둡니다.

현재 모델·스레드·캐시·native 경로는 [공유 설정](../configs/gguf_realtime.toml), 채택 근거·최신 결과·다음 작업은 [현재 상태](status.md), 연속 입력 옵션은 [대표 명령](guides/qwen3_optimization.md#현재-개발용-대표-명령)을 기준으로 합니다. 다른 안내에서는 이 문서들을 연결하고, 날짜가 있는 환경 스냅샷과 과거 실험 기록은 당시 값으로 보존합니다.
