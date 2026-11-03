# 문서 안내

[현재 상태와 다음 작업](status.md)을 먼저 보고 목적에 맞는 문서로 이동한다. 실제 수치의 원본은 [실측·검증 기록](../results/summaries/README.md)이다.

| 목적 | 문서 |
| --- | --- |
| WAV에서 자막 보기 | [WAV 자막 MVP](guides/live_captions.md) |
| 환경 설치·장비 동기화 | [설치](guides/setup.md), [Host/RP5 배포](guides/deployment.md) |
| 평가 입력 준비 | [데이터 출처·준비](guides/datasets.md), [manifest 명세](../benchmarks/manifest.md) |
| 모델 평가·재채점 | [공통 평가](guides/evaluation.md), [1배속 평가](guides/streaming_evaluation.md) |
| 코드 수정 | [아키텍처](reference/architecture.md), [테스트](../tests/README.md), [명령 목록](reference/tooling.md) |
| 지표 해석 | [측정 계약](reference/benchmark_design.md) |
| 실험을 재현하거나 결정 근거 찾기 | [연구 문서 색인](research/README.md), [실측 기록](../results/summaries/README.md) |
| 과거 환경·명령 이행 | [초기 검사](archive/verification.md), [폴더 통합](archive/consolidation.md), [옛 명령 이행표](archive/command_migration.md) |

문서별 역할은 한곳에 둔다. 사용법은 `guides/`, 코드/지표 계약은 `reference/`, 사전 계획·후보·판단 근거는 `research/`, 완료 수치와 원시 해시는 `results/summaries/`다. 과거 계획의 “다음 작업”보다 현재 [status.md](status.md)를 우선한다.
