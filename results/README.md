# 결과 보관

작은 검토용 요약은 [summaries/](summaries/README.md), 현재 채택한 기능/정책은 [프로젝트 상태](../docs/status.md)에서 확인한다. 개발 PC에 전체 원시 기록과 기존 실행 경로를 보존한다. RP5 사본은 사용처를 조사하고 SHA-256을 대조한 뒤 정리할 수 있다. [회수·정리 절차](../docs/guides/deployment.md#실험-결과-회수와-rp5-정리)

| 경로 | 내용 |
| --- | --- |
| `optimization/<단계>/<실행ID>/` | 새 Qwen OPT 원시 결과 |
| `summaries/optimization/` | 새 Qwen OPT 검토 요약 |
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

## 청취 음성의 이름과 날짜

2026-11-24에 Host의 `results/`에서 확인한 WAV 921개 중 파일명에 독립된 연월일이 들어간 것은 0개였다. 평가 기록은 실행 폴더에 날짜를 두고 그 아래에 원음 ID나 역할을 둔다. 예를 들어 `optimization/opt3/subtitle-continuity-20261123-01/mic-T/recording.wav`는 11월 23일 T 마이크 기록이다. `fixed-P8` 같은 파일 입력 실행은 manifest의 원음과 실행 이벤트를 함께 재생한다. 청취 캐시의 `listener/audio/<해시>/audio.wav`는 원음 식별용 캐시다. 따라서 파일명만 복사하면 날짜·조건이 사라진다.

원시 경로와 manifest 참조는 보존한다. 별도 청취용 사본을 내보낼 때는 `YYYYMMDD-실행조건-원음ID-순번.wav`로 통일한다. 예를 들어 11월 24일 P8 고정 평가의 원음 사본은 `20261124-fixed-P8-fleurs-1441445080190694850-01.wav`처럼 표기한다. 날짜는 해당 평가의 실행일이다.
