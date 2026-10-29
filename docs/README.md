# 문서 안내

처음 시작할 때는 설치 → 데이터 → 평가 → 배포 순서로 읽으세요. [현재 진행 상태](status.md)와 [실측 결과 목록](../results/summaries/README.md)은 설계·계획 문서와 구분합니다.

| 구분 | 문서 |
| --- | --- |
| 설치·일상 작업 | [환경 설치](guides/setup.md), [데이터 준비](guides/datasets.md), [공통 평가·재채점](guides/evaluation.md), [1배속 모사](guides/streaming_evaluation.md), [Host/RP5 배포](guides/deployment.md) |
| 코드·계약 | [아키텍처](reference/architecture.md), [실행 도구·명령 이행표](reference/tooling.md), [지표 정의](reference/benchmark_design.md), [M1 반복 측정](reference/rp5_repeat_benchmark.md) |
| M2 재현 절차 | [SenseVoice](research/m2_sensevoice.md), [통제 비교](research/m2_comparison.md), [외부 자료](research/m2_external_data.md) |
| M3 재현 절차 | [음량·냉각](research/m3_preflight.md), [VAD 후속](research/m3_vad_followup.md), [구간 상한](research/m3_segmentation.md), [공개 자료 준비](research/m3_public_vad_data.md), [구간 주석·예비 비교](research/m3_vad_annotations.md), [지속 음성·냉각 진단](research/m3_bounded_vad.md) |
| 과거 기록 | [초기 Host 검증](archive/verification.md), [이전 폴더 통합 절차](archive/consolidation.md) |

연구 절차의 미검증 항목과 당시 결과는 각 문서/보고서에서 명시합니다. 실행 전 현재 CLI의 `--help`를 확인하고, 과거 명령은 이행표에 따라 변환합니다. 원시 영수증의 명령·소스 해시는 당시 상태 그대로 보존합니다.
