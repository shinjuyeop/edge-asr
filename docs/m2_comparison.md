# M2-3 통제 모델 비교

이 단계는 기존 Zipformer·SenseVoiceSmall INT8의 batch 비교와 외부 자료 검증이다. Hybrid 런타임, Qwen, VAD 및 임의의 텍스트 필터를 추가하지 않는다. M1/M2-2 기록은 수정하지 않는다.

## 실행 전에 고정한 조건

- 기존 KSS 50개 WAV·정답·`ko_cer_v1`, 언어·ITN·가중치·출력 정리 정책을 유지한다. 각 모델의 기존 TOML에서 `num_threads`만 1/2/4로 바꾼 파생 설정을 원시 결과에 보관한다.
- 6개 구성을 3개 블록에서 교차 실행한다. 순서는 `scripts/compare_models.py`의 `plan_matrix()`와 각 실행의 `plan.json`에 고정한다. 한 실행은 새 프로세스/모델, 같은 첫 발화 워밍업 3회, 50개 순차 측정이다. 반복 150회는 독립 발화 150개가 아니다.
- 실행 직전 온도 55°C 이하를 요구한다. 최대 300초 대기 후 미충족/미계측이면 중단한다. 냉각·추론 중 1초 온도/CPU0 클럭/부하 표본과 전후 throttling 플래그를 보관한다. 클럭·팬·governor·affinity·패키지를 변경하지 않는다. 완전히 같은 열 상태나 파일 캐시 cold-start를 보증하지 않는다.
- 모든 ASR 자식은 외부 wall timeout 600초, TERM 이후 5초에 KILL, 가상 주소 공간 4GiB, core dump 0을 적용한다. 실제 평가 PID의 `/proc/PID/limits`를 확인한다. RP5에 memory cgroup controller가 없어 **RSS 4GiB 제한이 아니다**. 실패·timeout·미실행은 원시 로그/영수증으로 남긴다.
- 추론 CPU/시간 및 모델이 상주한 RSS는 기존 공통 평가기의 정의를 그대로 사용한다. 로딩·워밍업·파일 I/O는 추론 시간에서 제외한다. 로딩 자원은 따로 표시한다. CPU 100%는 코어 1개, `num_threads`는 전체 프로세스 CPU 상한이 아니다.
- 상세 TID별 CPU는 별도 6회 진단 실행에서 첫 측정 파일 완료 이후 3초 동안 관찰한다. 100ms 관찰 비용이 있는 진단 실행의 RTF는 주 비교에서 제외한다. 이 창은 파일 사이 기록/I/O를 포함하며 순수 커널 시간은 아니다. 이름/개수만으로 ONNX Runtime 세션 또는 spinning의 원인을 확정하지 않는다.

## 후보 선택과 외부 평가

모델별 모든 구성에서 3회 성공·입력/가설 일치를 확인한 뒤 중앙 corpus RTF가 최저인 구성의 5% 이내에 드는 것 중 설정 스레드가 가장 적은 구성을 선택한다. `selection.json`을 외부 추론 **전에** 고정한다. 이는 외부 batch 후보이며 동시 Hybrid 실행의 최적 설정을 뜻하지 않는다. CPU/RSS는 선택 근거와 함께 보고하고, 외부 결과를 보고 재선택하지 않는다.

외부 자료는 KSS와 출처가 다른 공개 한국어 test 자료를 사용한다. 출처 독립과 학습 데이터 비중복은 다르다. 완전한 학습 명세가 없으면 비중복 여부를 미확인으로 표시한다. 별도 문서화된 manifest/리비전/해시와 배포처 정답을 고정하며 CER은 기존 정책을 유지한다. 숫자 표기와 발음 표기의 차이도 임의 교정하지 않는다. 자료별 corpus CER과 실패/커버리지를 따로 보고한다.

무음은 빈 정답의 CER을 null로 두고 비어 있지 않은 반환 수·문자 수를 따로 집계한다. 실제 짧은 발화는 완전한 WAV/정답 쌍을 사용한다. 긴 입력은 원본 발화를 자르지 않고 연결한 약 30/60/120초 합성 스트레스 자료로 구분한다. 자연스러운 긴 대화 성능으로 일반화하지 않는다. 길이별 별도 실행으로 실패 영향 범위를 제한한다.

## 실행 방법

Host에서 전체 테스트 후 main commit/push, RP5에서 `git pull --ff-only`한다. 아래 출력 경로는 매번 새 이름을 사용한다. 기존 가상환경·모델은 재설치하지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_models.py matrix \
  --output results/comparisons/새실행ID/matrix
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_models.py select \
  --matrix results/comparisons/새실행ID/matrix \
  --output results/comparisons/새실행ID/selection.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_models.py diagnostics \
  --output results/comparisons/새실행ID/diagnostics
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_models.py selected \
  --selection results/comparisons/새실행ID/selection.json \
  --manifest data/evaluation/외부자료/manifest.jsonl \
  --output results/comparisons/새실행ID/external
```

외부 timeout 후 평가기 자체 manifest가 running으로 남을 수 있다. 외부 `execution.json`의 종료 코드와 함께 읽고, 부분 progress를 성공한 전체 실행으로 집계하지 않는다. 코드 변경 없이 원시 결과를 회수하고 SHA-256·Host 재채점 대조 및 기존 자산 보존 검사를 수행한다. Git에는 재현 코드·절차·작은 요약만 올린다.

## 해석 범위

batch RTF는 총 추론 처리 시간/총 오디오 길이다. 첫 부분 자막 시점, 입력 끝 기준 확정 지연, VAD endpoint 지연, Refine 큐 대기, UI 표시, 마이크 드롭은 이번 실험으로 측정되지 않는다. 이 값들은 null로 유지한다. Hybrid 채택 전 동시 실행 시 부분 자막 p50/p95, Refine 확정 지연·수정률, 지속 백로그/드롭, RSS/온도 및 무음 동작을 실제 입력 속도로 검증해야 한다.
