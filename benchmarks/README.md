# 벤치마크

Host에서는 평가 코드와 설정을 개발하고 RP5에서 실제 추론·성능을 측정합니다. 모델 상주 공통 batch 실행기·CER·재채점과 기존 프로브 반복 도구를 제공합니다. 새 공통 실행기의 실제 RP5 동작은 아직 미검증입니다.

- [공통 파일 평가](../docs/evaluation.md): JSONL 목록, 모델 1회 로드, CER·batch RTF·추론 CPU/RSS, 실패 보존과 재채점.

- [지표 정의 및 측정 절차](../docs/benchmark_design.md): CER, RTF, 첫 부분 자막 시점, 실제 인식 지연, 최종 확정 지연, 분위수, CPU/RSS, 드롭·백로그.
- [Manifest 명세](manifest.md): 향후 평가용 정답·오디오 목록. 현재 평가 데이터셋은 포함하지 않습니다.
- [기존 Zipformer 탐색 결과](../experiments/m1_zipformer/README.md): 단일 WAV, 각 스레드 1회 측정.
- [RP5 반복 실행](../docs/rp5_repeat_benchmark.md): 기존 프로브의 워밍업·교차 순서 반복, 지연·CPU/RSS, 실패 로그·요약 자동 보관.
- [사용자 제공 반복 요약](../results/summaries/zipformer-repeat-20261028-reported.md): 단일 WAV 스레드별 30회, 총 90/90 성공 보고. 후속 RP5 Codex 검사 보고에서 통합·횟수·자산·환경 일치를 확인했다고 보고됐으며 잠정 기준값은 2스레드입니다. Host 직접 실행·원본 파일 전송과 구분합니다.

공통 평가는 `results/evaluations/<run-id>/`, 재채점은 `results/scores/<run-id>/`, 프로브 반복은 `benchmarks/runs/<run-id>/`에 보관합니다(모두 Git 제외). 모델·WAV는 로컬 자산을 참조하고 작은 요약만 `results/summaries/`에서 검토해 버전 관리합니다.
