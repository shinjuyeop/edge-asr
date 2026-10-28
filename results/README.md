# 로컬 결과와 작은 요약

원시 로그·이벤트·환경 스냅샷·대용량 생성물은 하위 실행 폴더에 보관하며 Git에서 제외합니다.

공통 파일 평가는 `evaluations/eval-.../`, 재채점은 `scores/score-.../`에 고유 폴더를 생성합니다. 가설·정답을 포함한 predictions/scores JSONL도 원시 자료이며 자동 커밋하지 않습니다. 재채점은 원래 결과를 수정하거나 재추론하지 않습니다.

`summaries/` 바로 아래의 작은 `.md`, `.json`, `.csv`는 명시적으로 Git 추가할 수 있습니다. 자동 크기 제한은 없으므로 커밋 전 크기와 개인정보·전사 포함 여부를 검토합니다. 오디오·모델을 요약 파일에 인코딩해서 넣지 않습니다.

요약에는 실행 장비, Git 커밋, 모드, 샘플 수·반복 수, 실제 측정 범위와 미계측 필드, 원시 결과의 로컬 보관 위치를 남깁니다. 기존 보존 결과는 [실험 디렉터리](../experiments/m1_zipformer/README.md)에 유지합니다.

[2026-10-28 Zipformer 반복 측정](summaries/zipformer-repeat-20261028-reported.md)은 사용자가 전달한 RP5 `summary.md` 및 후속 [RP5 Codex 검사 보고](summaries/rp5-consolidation-audit-20261028.json)에서 파생한 기록입니다. 후속 보고로 환경·WAV 정보와 횟수·해시의 일치 여부를 보완했습니다. 원본 JSON·로그 자체의 전송과 Host 에이전트의 직접 검증은 아니며 지표별 n·원시 정밀도는 아직 미전달입니다.
