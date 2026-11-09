# Qwen 최적화 기록

새 OPT 실험의 검토 요약을 이 폴더에 보관합니다. **OPT-0 반복 기준선·OPT-1 병목 실측은 아직 완료하지 않았습니다.** 기존 Qwen 비교 결과는 [이전 기록](../README.md)에 원래 파일명으로 남습니다.

새 보고서는 단계·고유 실행 ID, Host 모의 검사/RP5 실제 추론 구분, 소스 커밋·환경/모델/입력 해시, 정확도·완료율·계측 범위·실패·한계를 명시합니다. 미계측 값은 `null`과 사유로 기록합니다. 원시는 `results/optimization/<단계>/<실행ID>/`에 두고 이 폴더에는 작은 `.md`/`.json`/`.csv` 요약만 추가합니다.

[연구 계획](../../../docs/research/qwen3_optimization.md), [현재 상태](../../../docs/status.md), [구조 이행](../../../docs/reference/structure_migration.md).
