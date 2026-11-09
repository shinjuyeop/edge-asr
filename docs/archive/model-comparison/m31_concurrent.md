# M3-1 Streaming + Refine 동시 부하 측정

2026-11-03 사전 고정 계획. 단독 결과로 동시 성능을 추정하지 않고 기존 WAV를 1배속 공급해 실제 경합을 측정한다. 결과 병합/화면/마이크 서비스는 구현하지 않는다. [현재 상태](../../status.md)와 이전 [구간 CER](../../../results/summaries/m3-segmented-accuracy-20261102.md)/[200ms 문맥](../../../results/summaries/m3-vad-context-20261103.md)을 유지한다.

## 조건과 자료

| 조건 | Zipformer 스레드 | SenseVoice 스레드 | 용도 |
| --- | --- | --- | --- |
| z1-only | 1 | 미실행 | 같은 조건의 단독 Streaming 대조군 |
| z1-s1 | 1 | 1 | 첫 동시 구성 |
| z1-s2 | 1 | 2 | Refine 속도/경합 비교 |
| z2-only | 2 | 미실행 | 2T 단독 대조군 |
| z2-s1 | 2 | 1 | Streaming 속도/경합 비교 |

모델/가중치/기존 TOML은 그대로 두고 유효 설정의 스레드 값만 기록한다. Zipformer 기존 encoder/joiner INT8·decoder·tail 0.66초, SenseVoice INT8·ko·ITN false·기존 태그 분리, 공통 `ko_cer_v1`을 유지한다. 각 모델은 실행마다 한 번 로딩한다. 자료 첫 발화의 앞 최대 5초를 각 모델에서 3회 비실시간 워밍업한다. 로딩/워밍업은 계측 구간에서 분리한다.

자료별 1회씩, 아래 순서를 결과 전에 고정한다. 오디오 공급 시간 합은 약 24분이다.

1. KSS 개발 50개를 원본 순서로 이어붙인 메모리 입력(164.919초): z1-only → z1-s1 → z1-s2 → z2-only → z2-s1.
2. 기존 120.5869초 합성 입력: 역순 z2-s1 → z2-only → z1-s2 → z1-s1 → z1-only.

원본 WAV·전사·해시는 유지하고 추가 gap/패딩 WAV를 만들지 않는다. 입력은 이미 확보한 자료이며 합성/개발용으로 표시한다. 두 자료의 정확도/지연을 합치지 않고 자연 대화/독립 평가로 부르지 않는다. 단회 실험이므로 재현 분산을 확정하지 않는다.

## 입력과 프로세스

기존 독립 입력 프로세스의 100ms·용량 5청크·drop_newest·10초 drain을 사용한다. 선택적 `chunk_handler`에 원본 절대 프레임 좌표를 전달하며 기존 `run_paced` 기본 호출은 유지한다. KSS 파일 경계에서는 Zipformer 상태를 새로 만들고 각각 최종 flush한다. 공통 청크가 파일 경계를 가로지르면 그 위치에서 나눈다. 같은 정책을 단독/동시 조건에 모두 적용하며 과거 파일별 개별 producer 실행과 직접 같은 조건으로 부르지 않는다.

Refine은 별도 spawn 프로세스에서 한 번 로딩한다. 원본 파일마다 고정 최대 5초·문맥 0의 연속 원음을 FIFO로 처리한다. KSS는 54구간, 120초 입력은 25구간이다. VAD 절취/200ms 문맥/중복 텍스트 제거/fallback은 없다. 파일 끝이나 5초가 도착하면 Zipformer의 해당 청크 decode 전에 Refine 작업을 비블로킹 제출한다.

Refine 큐는 대기 2구간(최대 10초), 처리 중 1구간은 별도다. 포화 시 해당 구간을 drop_newest로 기록하며 입력을 역압으로 늦추지 않는다. `put/get`과 이벤트 시각을 같은 짧은 잠금으로 보호하고 queue wait/백로그를 실제 이벤트에서 재구성한다. 입력 캡처 손실로 프레임이 끊기면 시간축을 압축하지 않고 해당 실행을 중단/실패로 남긴다. 누락이 있는 Refine CER는 null로 둔다.

## 지표와 검증

- 파일 시작→첫 비어 있지 않은 부분 반환: KSS 파일별 관측 p50/p95와 n/부분 출력 부재 수. 파일 시작은 실제 음성 시작 정렬이 아니다. 120초 입력은 n=1이므로 첫 부분 p50/p95는 null, 단일 관측값을 별도 본다.
- 파일 끝→Zipformer 최종 반환: 기존 tail/강제 flush 비용을 포함한다. 음향 endpoint 지연이 아니다.
- Refine 구간 끝→반환: 입력 전달/dispatch + 큐 대기 + 인식/결과 반환을 포함한다. queue wait와 decode 시간을 별도 기록한다. 원본 파일의 모든 구간이 끝난 최종 Refine 시점도 따로 기록한다. 실제 병합/자막 확정 지연으로 부르지 않는다.
- Streaming 최종 원본 파일 CER와 구간 전사를 순서대로 단일 공백 연결한 Refine CER를 따로 계산한다. 최종 Hybrid 병합 CER는 null이다. 입력/출력이 불완전하면 전체 CER/대표 지연을 성공값으로 채우지 않는다.
- 100ms 모델 PID CPU/RSS 표본, 1초 TID 표본, 외부 guard의 온도/실제 클럭/현재·이력 제한을 기록한다. 모델 PID 동시 표본 RSS 합과 입력 자식의 lifetime peak RSS를 분리한다. RSS 합은 공유 페이지를 중복 계산할 수 있으며 실제 RAM/PSS 총량과 다르다. 1코어=100%다.
- Refine job/Zipformer 호출의 실제 wall 시간 겹침도 기록한다. 겹친 호출 구간은 같은 순간 CPU 병렬 실행의 직접 측정값과 구분한다.

기본 열 보호를 유지한 `operational` guard, 600초 timeout·4GiB 주소 공간·코어 덤프 제한을 사용한다. main과 Refine 자식의 실제 PID 제한을 확인한다. 저전압/계측 결측/외부 제한 미확인/네이티브 실패는 성공으로 표시하지 않는다. 손실/처리 실패 원시도 보존하며, 하드웨어/계측 문제는 후속 실행을 멈추고 원인을 보고한다. 성능 포화로 인한 손실만 있으면 그 실패를 남기고 계획된 다른 조합은 계속 비교할 수 있다.

Host에서는 실제 모델 없는 spawn/CLI·PCM/창/파일 경계·유한 큐 포화·입력 드롭 좌표 거부·네이티브 오류 모의/회수·모델 없는 재채점·기존 전체 테스트를 확인한다. RP5에서는 실제 추론/환경/원본 해시 보존을 별도 확인한다. 결과는 main push→RP5 동기화→새 실행 ID로 남기며 기존 환경/모델/결과를 변경하지 않는다.

## 명령

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_실행> -- \
  .venv/bin/python scripts/edge_asr.py tools probe-concurrent \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --zip-threads 1 --refine-threads 1 --output <새_실행>

.venv/bin/python scripts/edge_asr.py tools summarize-concurrent \
  --run <원시_실행> --output <새_요약.json>
```

계측 모듈은 실험 전용이며 원문/태그와 모든 실패를 보존한다. 큐/메모리/스레드 수를 결과를 보고 조용히 바꾸거나 가장 유리한 발화만 골라 보고하지 않는다.

## 실제 실행 결과

위 계획을 `acf5136`에서 실행해 **10/10회 성공**, 입력/Refine 드롭 0을 확인했다. [2026-11-03 비교 보고서](../../../results/summaries/m31-concurrent-20261103.md)와 [전체 수치·증거 해시](../../../results/summaries/m31-concurrent-20261103.json)를 참고한다. 1T+2T는 다음 실험 후보지만 120초 합성 입력의 고정5초 Refine 정확도 회귀가 있어 자동 전사 교체는 채택하지 않는다. 실제 병합·VAD endpoint·마이크·장시간 운영은 별도 검증이다.
