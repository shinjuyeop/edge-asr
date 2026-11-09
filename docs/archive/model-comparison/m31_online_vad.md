# M3-1 온라인 VAD와 Streaming/Refine 동시 계측

2026-11-03 사전 고정 계획. [비실시간 구간 정책](../../../results/summaries/m3-context-split-20261103.md)과 구분해 **100ms씩 1배속으로 공급하는 WAV에 실제 VAD와 두 ASR을 함께 실행**한다. 오디오 재생 장치/마이크/자막 병합 서비스는 사용하지 않는다. 현재 목표는 프레임·구간 무결성과 실제 방출/대기/반환 지연이다.

## 조건과 순서

모든 실행은 Zipformer1T + SenseVoice2T, 기존 가중치/원음/정답/TOML·한국어/ITN false·`ko_cer_v1`을 유지한다. 실행별 유효 스레드만 기록한다. VAD는 기존 SHA의 k2 Silero·Python ORT1T·기존 임계값/상태/5초 상한이다. 네이티브 sherpa ORT와 VAD Python ORT 버전을 구분한다.

| `--refine-policy` | Refine 입력 |
| --- | --- |
| `fixed5` | 원본별 고정 최대5초/문맥0, 기존 동시 대조군 |
| `vad0` | 실제 온라인 bounded VAD/문맥0 |
| `vad-continuation200` | 같은 VAD 소유 구간, hard_limit 뒤 후속만 실제 과거200ms |

KSS 개발50개(164.919초/756자)는 표 순서, 기존120.5869초 합성1개(519자)는 역순으로 **총6회·각 조건1회** 실행한다. 입력 공급 총856.5176초(약14.3분)다. 입력은 이미 확보한 개발/스트레스 자료이며 자연 대화/최종 독립 평가가 아니다. 두 자료의 점수·지연은 합치지 않는다. 결과를 보고 유리한 발화만 남기거나 큐/문맥/설정을 바꾸지 않는다.

각 실행은 새 프로세스·모델1회 로딩·같은 자료 첫 발화의 앞 최대5초로 Zip/Refine 각각3회 워밍업한다. VAD 조건은 같은 원음으로 VAD만 추가3회 워밍업한다. 로딩/워밍업은 계측에서 제외한다. 기존 고정 경로의 기본 인자/요약은 유지하고 과거10회 재집계 불변을 확인한다.

## 온라인 입력과 유한 큐

독립 producer의 실제 단조시계 1배속 공급·100ms·대기5청크·drop_newest·10초 drain을 유지한다. Zip 입력은 모든 정책에서 같은 파일/5초 경계로 나눠 공급한다. 파일 끝에서 Zip 상태를 새로 만든다. VAD는 파일 경계 조각을 별도 버퍼에서 원본별100ms로 재조립해512프레임씩 실제 확률 추론한다. 입력을 미리 VAD로 분할해 그 계획만 재생하지 않는다.

재조립 버퍼는 최대3,199프레임, PCM ring은 최대99,200프레임이다. 소유 구간은 최대80,000프레임, 문맥 포함 payload는 최대83,200프레임이다. VAD 내부 파형 축적0·발화당 reset1회·hard_limit 중 reset0회를 유지한다. 소유/문맥/입력 공급 범위와 실제 payload 해시를 함께 기록한다. 소스 WAV 전체를 메모리에 읽는 계측 도구 전체의 무제한 입력 메모리 보증은 아니다.

Refine은 기존 spawn 프로세스에서 한 번 로딩한다. 실제 방출 callback이 호출되면 같은 청크의 Zip decode 전에 비블로킹 제출한다. 대기2구간·처리중1구간은 구분하고, 포화 시 drop_newest를 남긴다. 소유 오디오 큐 길이와 문맥을 포함한 payload 큐 길이를 구분한다. 입력 프레임 손실은 압축하지 않고 실패로 남긴다. Refine 누락은 전체 Refine CER를 null로 둔다. VAD가 내보낸 구간이0인 파일은 빈 가설로 원본 전체 정답에 채점하고 ASR fallback/금칙어/중복 제거는 하지 않는다.

## 지표 해석

- 원본 시작→Zip 첫 비어 있지 않은 부분 반환, 원본 끝→Zip 최종 반환을 기록한다. 음향 정렬/마이크/UI 지연은 아니다.
- 소유 구간 끝→VAD 방출 callback 진입, 방출→제출, 큐 대기, 인식 호출, 방출→인식 반환, 소유 끝→인식 반환을 각각 기록한다. 각 성분p95를 더해 전체p95로 만들지 않는다.
- VAD의 음향 프레임 결정 대기는 `emitted_at_audio_frame-owned_end_frame`이다. 벽시계 방출 대기에는 입력 격자·캡처 큐·main 처리·VAD/PCM 검증 비용이 포함된다. 두 값을 실제 음성 끝 정답 지연으로 해석하지 않는다.
- 마지막 Refine이 파일 끝보다 먼저 반환할 수 있다. 파일 끝 대비 시각은 음수 가능한 **상대 시각**으로 기록하며, 이를 음수 인식 지연이나 UI 확정으로 부르지 않는다. VAD 구간이 없는 파일의 Refine 반환 지연은 null이다.
- KSS 첫 부분은 원본별n50, 합성은n1이므로 합성 첫 부분p50/p95는 null이다. Refine 분위수에는 구간 수와 성공 관측 수를 표시한다.
- Zip 최종 CER와 Refine 구간을 단일 공백 연결한 CER를 따로 채점한다. 최종 병합 CER·정답 정렬 인식 지연·실제 음향 endpoint·마이크/UI는 null이다.
- 100ms PID CPU/RSS·약1초 TID·guard의 온도/현재·이력 제한/클럭을 기록한다. VAD 조건의 main PID에는 **Zip+VAD+분배/계측**이 공존한다. 독립 입력 PID와 모델 PID RSS 합을 구분하고 공유 페이지 중복 가능성을 명시한다. 로딩/워밍업·VAD512프레임 호출 비용은 별도다.

## 실행·검증·중단

Host 모델 없는 mock/spawn/CLI·좌표/문맥·무검출/원본 전체 정답·큐 포화/손실·오류 회수·파일 끝보다 이른 반환·재채점 검증 → main push → RP5 소스 동기화/단위 테스트 → 새 ID `m31-online-vad-20261103-01`로 실행한다. 기존 모델/가상환경/데이터/실험을 보존하고 새 파일만 만든다.

기존 `operational` guard의 기본 펌웨어 열 보호/입장≤53°C 3표본/600초 timeout/4GiB 주소 공간/코어 덤프 제한을 유지한다. 온도/감속만으로 중단하지 않는 승인된 정책이며 통제 클럭 비교가 아니다. 저전압·필수 계측 결측·제한 미확인·네이티브 실패는 후속 실행을 중단한다. 명확한 큐/입력 성능 손실만 있으면 실패 원시를 남기고 나머지 조건을 비교할 수 있다. 기존75/80°C 검사 결과나 기본 설정을 소급 수정하지 않는다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_실행> -- \
  .venv/bin/python scripts/edge_asr.py tools probe-concurrent \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --zip-threads 1 --refine-threads 2 --refine-policy vad0 --output <새_실행>

.venv/bin/python scripts/edge_asr.py tools summarize-concurrent \
  --run <원시_실행> --output <새_재집계.json>
```

원시 회수 후 입력/모델/가중치/설정·VAD 소유/문맥·실제 PCM/float payload·FIFO/손실·재집계와 기존 자산/환경 보존을 감사한다. 정확도는 앞선1T 비실시간 조건과 비교하되 이번2T 출력 차이를 숨기지 않는다. 성공하더라도 단회 WAV 실시간 모사이며 마이크·결과 병합·장시간 서비스 완료가 아니다.

## 실제 실행 결과

위 계획은 `79d447a`에서 **6/6회 성공·입력/Refine 드롭0**으로 완료했다. [본 비교 보고서](../../../results/summaries/m31-online-vad-20261103.md), [집계·증거 JSON](../../../results/summaries/m31-online-vad-20261103.json). VAD0의 소유 끝→Refine 반환p95는 KSS0.665초/120초 합성0.605초이며, 그중 VAD 방출 후 반환p95는0.306/0.273초다. 두 분위수를 더하거나 batch RTF와 같은 지표로 표시하지 않는다.

본6회 뒤 기존 디지털0 WAV6개에 고정/VAD0/후속을 각1회, 새 ID `m31-online-silence-20261103-01`로 **별도3회** 검사했다. 본 계획의 입력/조건을 수정하지 않았고 입력 공급 합은43.83초다. [별도 결과](../../../results/summaries/m31-online-silence-20261103.md)에서 고정4/6개 오출력과 VAD 두 조건의 방출/Refine 호출0을 확인했다. 빈 정답 CER·미호출 지연은 null이다. 별도 진단을 자연 잡음/독립 평가나 추가 반복 정확도 실험으로 합치지 않는다.
