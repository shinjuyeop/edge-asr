# M3 다음 평가: 자연 발화의 구간 처리와 CER

2026-11-02. 고정한 세 구간 정책의 평가/재채점 도구를 구현했다. Host mock 검증과 RP5 실제 실행을 구분해 결과 보고서에 기록한다. 과거 냉각 중단과 당시 설계만 완료한 원시는 보존한다. 이후 사용자 승인으로 [기본 열 보호 아래 8분 운영 검사](../../results/summaries/m3-operational-20261102.md)를 완료했으며, 이번 비교도 [운영 정책](m3_thermal_policy.md)을 사용한다. 온도/감속을 관찰하며 처리하고 저전압·필수 계측 누락·실행 실패는 성공으로 표시하지 않는다. 통제된 열 조건의 속도 비교나 Hybrid 서비스 검증은 아니다.

## 사람이 다시 전사하지 않고 확인할 수 있는 범위

기존 KSS 개발 50개와 배포 `expanded_script` 전사를 그대로 사용한다. `ko_cer_v1` 정규화 후 정답 756자, 총 164.919초이며 원본 WAV 해시 50개를 대조했다. FLEURS의 이미 본 결과·보류 화자 18개·판단 어려움인 청취 메모 5개는 사용하지 않는다.

KSS 전사는 **자료 제공자의 전사**이며 프로젝트에서 사람이 확정한 음소 경계가 아니다. 문장 전체 CER은 계산할 수 있지만 정확한 음성 끝·단어별 지연·구간별 정답 전사는 여전히 미확인이다. 제공 전사의 오류 가능성도 유지한다. 이 자료는 개발·회귀용이며 최종 독립 평가로 부르지 않는다.

50개 중 길이 3초 초과는 34개, 5초 초과는 4개, 8초 초과는 0개다. 5초 분할로 경계를 넘는 발화가 적다는 한계를 함께 보고한다. 기존 합성 장문으로 분할 분기를 확인할 수 있지만 자연 장문 결과와 합치지 않는다.

## 첫 단계의 사전 고정 조건

| 조건 | 입력 처리 | 확인 목적 |
| --- | --- | --- |
| full_batch | 완전한 원본 WAV를 기존 SenseVoice 어댑터에 공급 | 같은 시점의 기준 결과 |
| fixed_windows_5s | VAD 없이 5초씩 자르고 왼쪽 문맥 0 | 창 길이·모델 문맥 변화의 영향 |
| bounded_vad_5s | 공통 PCM→기존 bounded VAD→소유 상한 5초, 왼쪽 문맥 0 | VAD 생략과 구간 처리 전체의 영향 |

모두 SenseVoice INT8·1T·`ko`·ITN false·`ko_cer_v1`·공통 identity PCM을 사용한다. identity는 이번 비교의 고정 대조 정책이며 배포 승인 선언이 아니다. 모델/언어/태그 분리 정책을 바꾸지 않고 새 가중치·패키지를 설치하지 않는다. 동시 Zipformer·마이크·Refine 큐는 구현하지 않는다.

조건별 새 프로세스, 모델 한 번 로드, 같은 첫 발화의 ASR 워밍업 3회, 원본 manifest 순서로 50개를 각 1회 평가한다. VAD 조건은 같은 첫 발화로 VAD만 별도 3회 워밍업하며 ASR 호출을 추가하지 않는다. 비교 단위는 발화 150회이며 실제 ASR 호출 수는 구간 수에 따라 더 많을 수 있다. raw 전사·태그·입력 SHA·PCM SHA·구간 소유/문맥 프레임을 보존한다. 세그먼트마다 ASR 인식 상태만 새로 만들고 VAD/전처리 상태는 원본 발화 안에서 유지한다. VAD는 원본 발화 시작에만 reset하며 강제 5초 경계에서는 유지한다.

구간 텍스트는 순서대로 공백 한 개로 연결하고 중복 제거·금칙어·전사 교정·실패 시 전체 WAV fallback을 적용하지 않는다. 구간이 0개면 빈 가설로 남겨 해당 정답의 삭제 오류를 채점한다. VAD가 숨긴 음성을 정답에서 제외하지 않는다. 실행 오류는 실패/커버리지로 보존하고 성공처럼 채점하지 않는다.

초기에는 원본 발화당 CER·삽입/삭제/치환·완료율을 집계한다. `VAD5-full`, `fixed5-full`, `VAD5-fixed5` 차이를 보고하되 원인을 VAD 하나로 확정하지 않는다. 모델 문맥과 텍스트 연결도 결과를 바꿀 수 있다. 200ms 문맥·3/8초 후보는 후속 조건으로 분리하며 결과를 본 뒤 가장 유리한 조건만 남기지 않는다.

## 계측과 완료 기준

- 모델 로딩·워밍업·VAD 신경망 호출·ASR 호출·전체 파이프라인 비용을 분리한다. `summary.overall.rtf`는 전처리·구간화·ASR·연결·검증/계측을 포함한 파일 파이프라인 시간/원본 길이다. 별도 ASR RTF와 VAD 신경망 호출 비용은 그 부분집합이다. WAV I/O·검증용 참조 PCM 준비·로딩·워밍업·결과 파일 기록은 제외한다. VAD+ASR 모델이 공존하는 프로세스 RSS를 모델 하나의 메모리로 부르지 않는다.
- 빠른 파일 입력의 계산 시간과 RTF만 보고하고 실제 Streaming 첫 자막·VAD endpoint·Refine 확정 지연·드롭/백로그는 null/미지원 사유로 둔다.
- 동일 자료·동일 정답·동일 PCM 해시, 구간 경계/중복/상한, ASR 1회 로딩과 구간별 상태, 빈 검출/실패 보존, 기존 scorer 재채점을 Host mock으로 먼저 검증한다.
- main push→RP5 동기화→새 ID→`thermal-run --temperature-policy operational` 안에서 실행한다. 첫/중간/끝 온도·실제 클럭·현재/이력 제한과 실행 순서를 보존하고 고정 클럭 성능으로 부르지 않는다. SenseVoice 1T는 이전 설계에서 고정한 조건이며 Zipformer 2T 검사에 대신 끼워 넣지 않는다.
- 구현은 `tools/probes/segmented_accuracy.py`, `tools/reports/segmented_accuracy.py`, `tests/test_segmented_accuracy.py`다. 기존 어댑터·scorer·PCM/구간 계약을 재사용하며 플러그인이나 Hybrid 런타임을 추가하지 않는다.
- 세 조건의 모든 발화·실패·같은 입력의 재채점과 자원 구간을 확인해야 평가 도구 검증 완료다. CER 최저 조건을 자동 배포 승인하지 않으며 자연 음성 경계/실제 사용자 환경 검증은 남는다.

입력 검증과 고정 조건은 `results/maintenance/m3-cooling-recheck-20261102-01/segment-accuracy-plan.json`에 보존했다. 원시 입력과 전사는 새로 복제하지 않았고 기존 파일을 수정하지 않았다.

## 실행과 모델 없는 재채점

각 조건을 아래 명령으로 별도 실행한다. 순서는 `full_batch`, `fixed_windows_5s`, `bounded_vad_5s`이며 같은 폴더를 재사용하지 않는다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_실행>/<조건> -- \
  .venv/bin/python scripts/edge_asr.py tools probe-segmented-accuracy \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --condition <조건> --output <새_실행>/<조건>

# Host/RP5 어디에서든 원시 predictions만으로 재채점 가능
.venv/bin/python scripts/edge_asr.py tools summarize-segmented-accuracy \
  --predictions <원시_조건>/predictions.jsonl --output <새_재채점_폴더>
```

구간 평가 전용 재채점은 기존 `read_predictions`/`score_records`를 그대로 사용하고, 구간 경계/연결 정책과 전체/5초 초과 부분집합을 추가 검사한다. 저장한 PCM 해시의 원음 바이트 대조는 원본을 가진 별도 감사에서 수행한다. 재채점만으로 모델·음원·해시의 진위를 새로 검증했다고 부르지 않는다.
