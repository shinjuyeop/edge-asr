# 벤치마크 안내

평가 코드는 `src/edge_asr/`에서 공유하고 실제 추론은 RP5에서 수행한다. 이 폴더는 입력 명세와 초기 M1 프로브의 결과 경로를 보존한다. 현재 Qwen 개발은 [Qwen 실행 안내](../docs/guides/qwen3_optimization.md)에서 시작한다.

| 필요한 내용 | 문서 |
| --- | --- |
| 오디오·정답 목록 | [manifest 명세](manifest.md), [데이터 준비](../docs/guides/datasets.md) |
| batch 평가·저장 가설 재채점 | [공통 평가](../docs/guides/evaluation.md) |
| 1배속 평가·큐·지연 | [paced_stream](../docs/guides/legacy/streaming_evaluation.md) |
| 이전 Zip 자막 재현 | [WAV MVP](../docs/guides/legacy/live_captions.md) |
| CER/RTF/CPU/RSS의 범위 | [측정 계약](../docs/reference/benchmark_design.md) |
| M1 단일 샘플 반복 재현 | [기존 프로브](../experiments/m1_zipformer/README.md), [반복 도구](../docs/archive/model-comparison/rp5_repeat_benchmark.md) |
| 완료한 측정과 한계 | [검증 기록](../results/summaries/README.md) |

새 공통 평가의 원시는 `results/`에, M1 반복 프로브는 `benchmarks/runs/`에 저장한다. 모두 Git 제외이며 [결과 보관 정책](../results/README.md)을 따른다.
