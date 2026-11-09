# 문서 안내

[현재 상태](status.md)와 [Qwen 최적화 연구 계획](research/qwen3_optimization.md)이 활성 작업의 시작점입니다.

| 목적 | 문서 |
| --- | --- |
| 새 프로젝트 방향·단계 | [OPT-0~4](research/qwen3_optimization.md), [현재 연구 색인](research/README.md) |
| 실행 준비 | [Qwen 최적화 시작](guides/qwen3_optimization.md), [설치](guides/setup.md), [배포](guides/deployment.md) |
| 평가 기반 | [데이터](guides/datasets.md), [공통 평가](guides/evaluation.md), [manifest](../benchmarks/manifest.md) |
| 코드·경로 | [아키텍처](reference/architecture.md), [이번 구조 이행](reference/structure_migration.md), [도구](reference/tooling.md), [테스트](../tests/README.md) |
| 계측 정의 | [측정 계약](reference/benchmark_design.md) |
| 새 OPT 요약 | [optimization 기록](../results/summaries/optimization/README.md) |
| 이전 연구·실측 | [모델 비교 보관 문서](archive/model-comparison/README.md), [기존 결과](../results/summaries/README.md) |
| 이전 자막 사용 | [legacy WAV 자막](guides/legacy/live_captions.md), [Zip 1배속 평가](guides/legacy/streaming_evaluation.md) |
| 더 이전 이행 기록 | [초기 검사](archive/verification.md), [폴더 통합](archive/consolidation.md), [옛 명령](archive/command_migration.md) |

사용법은 `guides/`, 코드/계측 계약은 `reference/`, 활성 계획은 `research/`, 이전 계획은 `archive/`, 측정값과 검증 결과는 `results/summaries/`에 둡니다. 이전 계획의 다음 작업보다 현재 상태를 우선합니다.
