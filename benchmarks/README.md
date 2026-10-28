# 벤치마크

Host에서는 평가 코드와 설정을 개발하고 RP5에서만 추론·성능을 측정합니다. 보존된 프로브의 반복 실행기는 제공하며 모델 상주 방식의 공통 실행기는 미구현입니다.

- [지표 정의 및 측정 절차](../docs/benchmark_design.md): CER, RTF, 첫 부분 자막 시점, 실제 인식 지연, 최종 확정 지연, 분위수, CPU/RSS, 드롭·백로그.
- [Manifest 명세](manifest.md): 향후 평가용 정답·오디오 목록. 현재 평가 데이터셋은 포함하지 않습니다.
- [기존 Zipformer 탐색 결과](../experiments/m1_zipformer/README.md): 단일 WAV, 각 스레드 1회 측정.
- [RP5 반복 실행](../docs/rp5_repeat_benchmark.md): 기존 프로브의 워밍업·교차 순서 반복, 지연·CPU/RSS, 실패 로그·요약 자동 보관. 실제 RP5 실행은 미검증입니다.

새 실행의 원시 결과는 `benchmarks/runs/<run-id>/`에 보관합니다(Git 제외). 모델과 WAV는 `models/`, `data/` 또는 RP5의 기존 읽기 경로를 참조합니다. 작은 요약은 `results/summaries/`에 작성합니다. 실행이 없으면 보고서에 미실행으로 표기하세요.
