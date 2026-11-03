# M3-1 후속: 음성 시작 여유와 강제 분할 문맥 분리

2026-11-03 사전 계획. [동시 부하 측정](../../results/summaries/m31-concurrent-20261103.md)에서 고정5초 Refine은 120초 합성 입력의 오류가 95자로 Streaming 58자보다 많았다. 과거 VAD0/일괄200ms는 같은 입력에서 32/37자였지만 실행 조건이 달랐다. 이번에는 동일 실행 조건에서 구간 정책을 먼저 비교한다. 결과 병합·실시간 자막 서비스는 구현하지 않는다.

## 후보와 대조군

| 조건 | 200ms 실제 과거 원음 적용 위치 |
| --- | --- |
| `full_batch` | 없음, 전체 발화 1회 |
| `fixed_windows_5s` | 없음, 원본 시작부터 고정 최대5초 |
| `bounded_vad_5s` | 없음, 기존 VAD 소유 구간 |
| `bounded_vad_5s_onset200ms` | VAD가 새 음성 구간을 시작한 첫 payload에만 적용 |
| `bounded_vad_5s_continuation200ms` | 같은 음성 구간을 hard_limit으로 나눈 뒤 후속 payload에만 적용 |
| `bounded_vad_5s_context200ms` | 모든 VAD payload에 적용, 기존 후보 |

VAD의 임계값·상태·소유 경계·소유 상한5초는 바꾸지 않는다. 자연 종료/EOF로 닫힌 구간 뒤에는 새 음성으로 분류한다. 강제 확정한 끝보다 VAD 종료점이 앞서 마지막 tail이 생략되는 경우에도 새 음성 구간의 시작 여유가 후속 문맥으로 오분류되지 않도록 span ID를 기록한다. 발화 중 VAD reset·추가 미래 입력·인위 무음·텍스트 중복 제거·금칙어·fallback은 없다.

후보는 기존 `SegmentPipeline`에 선택 인자 `context_selection`만 추가한다. 기본 `all`은 과거와 같은 경로/메타데이터를 유지한다. 새 선택에서만 span ID·후속 여부·적용 문맥을 기록한다. 소유 구간은 최대80,000프레임, payload는 최대83,200프레임이다. PCM ring의 기존 유한 상한을 유지한다.

## 입력·순서·환경

기존 KSS 개발50개(164.919초/756자)와 기존 합성32/60/120초 3개(213.2677초/910자)를 별도로 집계한다. 원본/정답/manifest/모델은 수정하지 않는다. 이미 결과를 본 개발/스트레스 자료이며 자연 장문·최종 독립 평가가 아니다. KSS는 위 표 순서, 합성 장문은 역순으로 **각 조건 1회, 총12회/318개 원본 평가**한다. 순서를 결과 전에 고정하고 개선 발화만 선택하지 않는다.

SenseVoice INT8 **1T**·ko·ITN false·identity·`ko_cer_v1`, 기존 k2 Silero SHA와 Python ORT1T를 유지한다. 정확도 분리 검사는 과거1T 기준과 맞추며 1+2 동시 성능으로 부르지 않는다. 새 프로세스/모델1회 로딩, 동일 자료 첫 발화로 ASR3회·VAD 조건은 VAD만 추가3회 워밍업한다. 로딩/워밍업·ASR/VAD 호출·파일 파이프라인 비용·CPU/RSS/열을 분리한다.

Host 테스트/과거 원시 재채점 → main push → RP5 소스 동기화/단위 테스트 → 새 ID `m3-context-split-20261103-01`에서 기존 operational guard로 실행한다. 기본 펌웨어 열 보호/600초 timeout/4GiB 주소 공간/코어 덤프 제한을 유지한다. 저전압·필수 계측 결측·실행 실패·외부 제한 미확인 시 후속 실행을 중단하고 원시를 보존한다. 팬/OS/전압/클럭/패키지/기존 `.venv`를 변경하지 않는다.

## 검증과 판정

- 4개 VAD 조건의 소유 경계·원음/문맥 payload 해시·구간/프레임 순서가 같아야 한다. 실제 hard_limit/후속 payload가 없으면 그 항목의 정확도 효과는 미검증으로 기록한다.
- 자료별 CER S/D/I, 발화별 개선/악화/동률, 길이별·사전 회귀 사례, 빈 검출/무음 출력/실패를 모두 남긴다. 구간 텍스트는 단일 공백으로 연결하고 전체 원본 정답으로 채점한다.
- 두 선택 후보의 효과를 같은 VAD0/일괄200ms 대조군과 비교한다. 개발 CER의 작은 차이만으로 정책을 자동 채택하지 않는다. 120초 한 입력의 후속 구간 효과를 장문 일반 성능으로 확대하지 않는다.
- 원시를 Host로 회수해 모델 없이 재채점하고 입력/가중치/과거 결과·환경 보존을 대조한다. Host mock 테스트와 실제 RP5 모델 실행을 구분한다.

이번 단계는 정확도 정책 검증이다. 실시간 첫 부분·VAD 음향 endpoint·실제 Refine 대기/반환·드롭/백로그·최종 병합 CER는 이번 비실시간 파일 평가에서 **null**이다. 그다음에는 정확도 회귀가 검토된 VAD 후보를 1T+2T 동시 측정 도구에 연결해 온라인 VAD 비용과 유한 큐를 별도로 확인한다. 사전 VAD 결과를 읽어 실제 VAD 비용을 누락한 실행은 하지 않는다.

## 실제 결과와 재현 명령

위 계획을 `70169c1`에서 실행해 **12/12회·318/318개 원본 평가**를 완료했다. [비교 보고서](../../results/summaries/m3-context-split-20261103.md), [수치·증거 JSON](../../results/summaries/m3-context-split-20261103.json). 시작200ms는 KSS1자 개선/합성12자 악화, 후속200ms는 실제 적용1회에서1자 개선이었다. 자동 채택하지 않으며 다음 온라인 계측은 VAD0과 후속 후보를 대조한다.

```bash
# RP5, 기존 모델/데이터가 있을 때 새 출력 경로로 실행
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_실행> -- \
  .venv/bin/python scripts/edge_asr.py tools probe-segmented-accuracy \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --condition bounded_vad_5s_continuation200ms --output <새_실행>

# Host, 모델 없이 원시 재채점
.venv/bin/python scripts/edge_asr.py tools summarize-segmented-accuracy \
  --predictions <원시_실행>/predictions.jsonl --output <새_재채점_폴더>
```
