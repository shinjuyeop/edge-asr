# 결과 보관

작은 검토용 요약은 [summaries/](summaries/README.md), 현재 채택한 기능/정책은 [프로젝트 상태](../docs/status.md)에서 확인한다. 원시 측정값은 기존 실행 폴더에 그대로 보존한다.

| 경로 | 내용 |
| --- | --- |
| `evaluations/eval-*/` | 공통 batch/paced_stream 평가·원문 가설·이벤트 |
| `scores/score-*/` | 저장 가설 재채점 |
| `live/wav-*/`, `live/<실행ID>/` | WAV 자막·stdout·모델/열 계측 |
| `preflight/<실행ID>/` | 모델 비교·VAD·동시 부하 실험 |
| `merge-replays/<실행ID>/` | 저장 기록의 병합/교체 정책 재생 |
| `maintenance/<실행ID>/` | 계획·테스트·자산 보존 감사·전송 검증 |
| `summaries/` | 검토해 버전 관리하는 `.md`/`.json`/`.csv` |

요약 이외의 하위 폴더는 Git에서 제외한다. predictions/scores JSONL도 정답·가설을 포함한 원시다. 오디오·모델·원문을 요약 안에 인코딩하지 않는다. 자동 크기 제한은 없으므로 커밋 전 크기와 개인정보·전사 포함 여부를 확인한다.

새 실행/재채점에는 고유 ID를 사용하고 기존 경로는 덮어쓰지 않는다. 요약에는 장비·소스 커밋·모드·표본/반복 수·계측 범위·미측정 항목·원시 경로/해시를 남긴다. 새 기기에서 재채점한 것은 속도 재측정이 아니다. 실패·미실행·사용자 제공 수치와 직접 실측을 구분한다.

초기 단일 샘플 원본의 보존 위치는 [experiments](../experiments/README.md), 과거 RP5 폴더는 [통합 기록](../docs/archive/consolidation.md)을 참고한다. 새 원시를 회수할 때는 기존 파일을 덮어쓰지 않고 전송 후 해시를 대조한다.
