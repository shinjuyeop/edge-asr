# M2-3 통제 모델 비교

이 단계는 기존 Zipformer·SenseVoiceSmall INT8의 batch 비교와 외부 자료 검증이다. Hybrid 런타임, Qwen, VAD 및 임의의 텍스트 필터를 추가하지 않는다. M1/M2-2 기록은 수정하지 않는다.

## 실행 전에 고정한 조건

- 기존 KSS 50개 WAV·정답·`ko_cer_v1`, 언어·ITN·가중치·출력 정리 정책을 유지한다. 각 모델의 기존 TOML에서 `num_threads`만 1/2/4로 바꾼 파생 설정을 원시 결과에 보관한다.
- 6개 구성을 3개 블록에서 교차 실행한다. 순서는 `scripts/edge_asr.py tools compare`의 `plan_matrix()`와 각 실행의 `plan.json`에 고정한다. 한 실행은 새 프로세스/모델, 같은 첫 발화 워밍업 3회, 50개 순차 측정이다. 반복 150회는 독립 발화 150개가 아니다.
- 실행 직전 온도 55°C 이하를 요구한다. 최대 300초 대기 후 미충족/미계측이면 중단한다. 냉각·추론 중 1초 온도/CPU0 클럭/부하 표본과 전후 throttling 플래그를 보관한다. 클럭·팬·governor·affinity·패키지를 변경하지 않는다. 완전히 같은 열 상태나 파일 캐시 cold-start를 보증하지 않는다.
- 모든 ASR 자식은 외부 wall timeout 600초, TERM 이후 5초에 KILL, 가상 주소 공간 4GiB, core dump 0을 적용한다. 실제 평가 PID의 `/proc/PID/limits`를 확인한다. RP5에 memory cgroup controller가 없어 **RSS 4GiB 제한이 아니다**. 실패·timeout·미실행은 원시 로그/영수증으로 남긴다.
- 추론 CPU/시간 및 모델이 상주한 RSS는 기존 공통 평가기의 정의를 그대로 사용한다. 로딩·워밍업·파일 I/O는 추론 시간에서 제외한다. 로딩 자원은 따로 표시한다. CPU 100%는 코어 1개, `num_threads`는 전체 프로세스 CPU 상한이 아니다.
- 상세 TID별 CPU는 별도 6회 진단 실행에서 첫 측정 파일 완료 이후 3초 동안 관찰한다. 100ms 관찰 비용이 있는 진단 실행의 RTF는 주 비교에서 제외한다. 이 창은 파일 사이 기록/I/O를 포함하며 순수 커널 시간은 아니다. 이름/개수만으로 ONNX Runtime 세션 또는 spinning의 원인을 확정하지 않는다.

## 후보 선택과 외부 평가

모델별 모든 구성에서 3회 성공·입력/가설 일치를 확인한 뒤 중앙 corpus RTF가 최저인 구성의 5% 이내에 드는 것 중 설정 스레드가 가장 적은 구성을 선택한다. `selection.json`을 외부 추론 **전에** 고정한다. 이는 외부 batch 후보이며 동시 Hybrid 실행의 최적 설정을 뜻하지 않는다. CPU/RSS는 선택 근거와 함께 보고하고, 외부 결과를 보고 재선택하지 않는다.

2026-10-28 실행에서는 이 자동 조건이 충족되지 않았다. Zipformer 4스레드의 `kss-train-01008` 가설이 1/2스레드와 달랐으며 구성 안 3회는 동일했다. 오류는 88→91개로 증가했다. 자동 선택은 실제로 오류를 반환했고 로그를 보존했다. 이후 외부 추론 전에 입력·가중치·ASR 소스·스레드 이외 설정과 구성 내 3회 가설 일치를 확인하고, **모델별 KSS 최소 오류 구성 중 위 RTF 기준**을 적용했다. 이 사후 개발 자료 검토와 정책 변경을 `review_selection.py`/`selection.json`에 기록했으며 Zipformer 2·SenseVoice 4를 고정했다. 외부 자료를 본 뒤 선택한 결과가 아니다. 스레드별 가설 차이의 네이티브 연산 원인은 미확인이다.

외부 자료는 KSS와 출처가 다른 공개 한국어 test 자료를 사용한다. 출처 독립과 학습 데이터 비중복은 다르다. 완전한 학습 명세가 없으면 비중복 여부를 미확인으로 표시한다. 별도 문서화된 manifest/리비전/해시와 배포처 정답을 고정하며 CER은 기존 정책을 유지한다. 숫자 표기와 발음 표기의 차이도 임의 교정하지 않는다. 자료별 corpus CER과 실패/커버리지를 따로 보고한다.

무음은 빈 정답의 CER을 null로 두고 비어 있지 않은 반환 수·문자 수를 따로 집계한다. 실제 짧은 발화는 완전한 WAV/정답 쌍을 사용한다. 긴 입력은 원본 발화를 자르지 않고 연결한 약 30/60/120초 합성 스트레스 자료로 구분한다. 자연스러운 긴 대화 성능으로 일반화하지 않는다. 길이별 별도 실행으로 실패 영향 범위를 제한한다.

## 실행 방법

Host에서 전체 테스트 후 main commit/push, RP5에서 `git pull --ff-only`한다. 아래 출력 경로는 매번 새 이름을 사용한다. 기존 가상환경·모델은 재설치하지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools compare matrix \
  --output results/comparisons/새실행ID/matrix
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools compare select \
  --matrix results/comparisons/새실행ID/matrix \
  --output results/comparisons/새실행ID/selection.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools compare diagnostics \
  --output results/comparisons/새실행ID/diagnostics
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools compare selected \
  --selection results/comparisons/새실행ID/selection.json \
  --manifest data/evaluation/외부자료/manifest.jsonl \
  --output results/comparisons/새실행ID/external
```

외부 timeout 후 평가기 자체 manifest가 running으로 남을 수 있다. 외부 `execution.json`의 종료 코드와 함께 읽고, 부분 progress를 성공한 전체 실행으로 집계하지 않는다. 코드 변경 없이 원시 결과를 회수하고 SHA-256·Host 재채점 대조 및 기존 자산 보존 검사를 수행한다. Git에는 재현 코드·절차·작은 요약만 올린다.

비교 CLI는 자식 실패의 영수증을 남기고 같은 단계의 나머지 구성을 실행한 뒤 하나라도 실패/미계측이면 종료 코드 1을 반환한다. 냉각 실패 등 컨트롤러 오류는 즉시 중단한다. 이 종료 코드 전파 보완은 이번 실측 후 Host 테스트로 검증했으며, 성공한 36개 실측을 재실행하거나 소급 변경하지 않았다.

자동 선택이 거부되면 이를 우회해 성공으로 기록하지 않는다. 이번 실행처럼 KSS 내부에서 차이를 검토한 선택은 자동 결과와 구분한 검토 기록이 필요하다. 원시 결과 회수 후 `scripts/edge_asr.py tools summarize-comparison <campaign> --output <새 JSON>`으로 공통 scorer 재채점 일치·모델 바이트·단계 사이 ASR 소스 일치를 검사하고 요약할 수 있다.

## 해석 범위

batch RTF는 총 추론 처리 시간/총 오디오 길이다. 첫 부분 자막 시점, 입력 끝 기준 확정 지연, VAD endpoint 지연, Refine 큐 대기, UI 표시, 마이크 드롭은 이번 실험으로 측정되지 않는다. 이 값들은 null로 유지한다. Hybrid 채택 전 동시 실행 시 부분 자막 p50/p95, Refine 확정 지연·수정률, 지속 백로그/드롭, RSS/온도 및 무음 동작을 실제 입력 속도로 검증해야 한다.

[ONNX Runtime 공식 스레드 문서](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)는 세션별 intra-op 풀과 spinning을 설명한다. 여러 세션의 풀·대기가 CPU 사용에 기여할 가능성은 있지만, 현재 sherpa 네이티브 런타임의 TID 역할을 이 관찰만으로 확정하지 않는다. 별도 Python `onnxruntime` 패키지 설정을 바꾸어 sherpa 내부 세션을 제어했다고 간주하지 않는다.

이번 온도 통제는 냉각 대기의 마지막 표본이 55°C 이하라는 조건이다. KSS 18회 중 2회는 이어지는 실행 직전 별도 표본에서 55.1°C를 기록했다. 이 편차를 요약에 표시하며 과거 기록을 고치지 않는다. 실행 중 열 제한도 발생했으므로 결과를 고정 클럭의 모델 고유 성능으로 해석하지 않는다. `get_throttled`의 현재 상태 비트와 누적 이력 비트를 구분한다([Raspberry Pi 공식 문서](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled)). `0xe0000`은 주파수 제한·throttling·soft 온도 제한의 이력이며, 그 자체로 모든 후속 실행에서 현재 throttling 중이라는 뜻은 아니다.
