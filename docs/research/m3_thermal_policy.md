# M3 열 정책: 통제 비교와 실제 운영 검사

## 운영 검사: 기본 열 보호 아래 계속 실행

실측은 [8분 운영 부하 기록](../../results/summaries/m3-operational-20261102.md)에 정리했다. 감속/스로틀링을 포함해 1,203회 추론을 완료했으며 통제 비교 및 실시간 지연 검증과 구분한다.

2026-11-02 사용자가 온도만으로 진행을 막지 않는 운영 검사를 승인했다. `--temperature-policy operational`을 추가한다. **소프트웨어의 독립 온도 중단선은 없으며 RP5의 기본 펌웨어 열 보호·팬·클럭·전압 설정을 그대로 유지한다.** 80°C 이상과 감속은 관찰값이며 통제 비교 합격 여부와 별개다. 제조사의 온도 제한을 해제하거나 올리는 명령은 실행하지 않는다. [공식 열 제어 설명](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#frequency-management-and-thermal-control).

- 시작은 기존과 같이 53°C 이하·현재 제한 없음 3회 연속이다. 과거 제한 이력은 기준선으로 보존한다.
- 80°C 이상을 `warnings.jsonl`에 기록한다. 현재 주파수 제한·throttling·soft temperature limit(비트 1~3)과 그 새 이력은 중단 사유가 아니다. 원시 `thermal.jsonl`의 모든 표본에서 관찰한다.
- 현재 저전압(비트 0), 새 저전압 이력(비트 16), 필수 계측 누락은 중단한다. 기존 timeout 600초·주소 공간 4GiB·코어 덤프 생성 제한·실제 PID 제한 검증도 유지한다.
- 폴링 대기 1초와 조회 비용을 기록한다. sysfs `scaling_cur_freq`와 `vcgencmd measure_clock arm`의 실제 클럭을 구분하고, 실제 클럭 조회 실패는 null로 남긴다. 각 조회는 순차 실행이며 완전히 같은 순간의 표본이 아니다.
- 영수증에 `evaluation_scope=operational_sustained_load`, `policy.stop_mc=null`, `allow_thermal_limits=true`를 저장한다. 정상 종료·PID 제한 확인·중단 조건 부재 때만 `operational_valid=true`다. **`control_valid`는 항상 false**로 남겨 기존 통제 비교 집계에서 제외한다. 저전압 등 실패는 `operational_rejected`, 실행 실패·시간 초과·제한 미확인은 `failed_or_unverified`다.

이번 계획은 같은 Zipformer 2T·KSS 개발 50개·원본 해시·워밍업 3회·480초 연속 batch 부하 1회다. 모델은 한 번만 로딩한다. 새 실행 ID와 maintenance 폴더를 사용하고 Host/RP5 단위 테스트 후 main 소스를 동기화한다. SSH와 독립된 기존 guard 하나만 실행한다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_실행> -- \
  .venv/bin/python -u scripts/edge_asr.py tools probe-load \
  --config configs/zipformer_eval.toml \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --duration 480 --output <새_실행>
```

호출별 monotonic 시작/종료와 추론 구간 시작 시각을 추가해 열 표본·TID 표본과 연결한다. 처음과 마지막 **완료한 50개 순환**의 같은 ID를 비교하고 전체/순환별 진단 batch RTF·CPU·RSS를 보고한다. 워밍업·로딩은 분리하고 미완료 호출은 완료 집계에 넣지 않는다. 감속 여부·그때의 속도를 함께 해석하며 고정 클럭 성능으로 표시하지 않는다. 이 부하는 오디오보다 빠르게 반복 처리하므로 실제 1배속 streaming 지연·입력 드롭·백로그·Hybrid 지연은 미계측이다. CER도 새로 측정하지 않는다. 8분 완료는 더 긴 운영이나 실시간 합격을 대신하지 않는다.

## 운영 검사의 별도 시작 조건

2026-11-06 추가: 기본 시작53°C는 유지하며, `operational`에서만 `--admission-temperature-c 60`을 명시할 수 있다. [파일 기반 운영 검사](file_validation.md)의 새 실행에 적용하는 별도 출발 조건이다. 현재 제한없음3회·실제 시작 표본·기존 중단/외부 제한은 유지하고, 기존53°C 기록과 같은 조건으로 합치지 않는다. 이 옵션은 펌웨어 온도 제한을 변경하지 않는다.

## 과거 별도 정책: 75°C 경고 / 80°C 중단

2026-11-02 사용자가 공식 온도 기준 설명 후 이 조건의 구현·실험을 승인했다. 기존 75°C 결과를 새 기준으로 재판정하지 않고 별도 실행 ID에서 검증한다. 하드웨어/팬 설정·클럭·전압·모델·패키지는 변경하지 않는다.

실측은 [새 정책 검증 기록](../../results/summaries/m3-thermal80-20261102.md)에 정리했다. 75°C 경고 후 실행됐지만 해당 2T 구성은 80.95°C에서 중단해 장시간 조건은 미통과했다. 아래는 구현 시 고정한 정책과 재현 절차다.

### 선택과 기록

`thermal-run`의 기본값 `--temperature-policy legacy75`는 과거 재현용으로 유지한다. 이 경우 75°C 중단·폴링 대기 1초와 기존 영수증 형식을 유지한다. 당시 별도 실험에는 `--temperature-policy warn75-stop80`을 명시했다. 이후 운영 부하는 위 `operational` 정책을 선택한다.

| 항목 | legacy75 | warn75-stop80 |
| --- | --- | --- |
| 시작 조건 | 53°C 이하·현재 제한 없음 3회 연속 | 동일 |
| 온도 경고 | 별도 기록 없음 | 75°C 이상, 그 이유만으로 중단하지 않음 |
| 온도 중단 | 75°C 이상 관측 | 80°C 이상 관측 |
| 현재 제한·새 이력·필수 계측 누락 | 중단 | 동일하게 중단 |
| 폴링 사이 대기 | 1초 | 0.25초 |
| 외부 제한 | 600초 timeout·4GiB 주소 공간·코어 덤프 0 | 동일, 실제 PID 확인 필요 |

0.25초는 조회 이후의 대기이며 정확한 표본 간격 보증이 아니다. sysfs/`vcgencmd`/TID 조회 시간이 추가되고 원시 단조 시각으로 실제 간격을 확인한다. 80°C에 절대 도달하지 않는다는 보장도 아니다. 관측 시점의 초과·제한은 그대로 실패로 남긴다. 새 이력이 켜진 실험은 다음 실행 전에 기준선을 재검토하며 무조건 반복하지 않는다.

새 영수증은 `policy.name`, `warn_mc=75000`, `stop_mc=80000`, `interval_s=0.25`를 기록한다. `warnings.jsonl`에 monitor/after 단계의 경고 표본을 보존하고 `warnings.sample_count`와 첫 표본을 기록한다. after 표본도 경고·중단 검사에 포함한다. 경고만 발생하고 나머지 조건을 통과하면 `control_valid=true`가 가능하다. 현재 제한/새 이력/결측을 온도 경고로 낮추지 않는다.

새 조건은 [공식 열 제어 설명](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#frequency-management-and-thermal-control)과 [75°C 최대 팬 제어](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#raspberry-pi-5-fans)를 참고한 프로젝트 측정 정책이며 제조사의 무조건적 안전 보증이 아니다. 팬 곡선 자체는 쓰거나 변경하지 않는다.

### 당시 실제 검사 계획

기존 Zipformer 2T·tail padding 0.66초·KSS 개발 50개·워밍업 3회·최대 480초 연속 부하를 그대로 사용한다. 모델/manifest/설정 해시를 과거 실행과 확인한다. NN 로딩과 순수 호출 비용을 분리하고 완료한 50개 단위 반복이 충분할 때만 초반/후반 동일 발화를 비교한다. 중단된 부분 호출로 통제 RTF·장기 저하율·CER을 만들지 않는다.

```bash
# RP5 /home/deepet/projects/edge-asr, clean main에서 새 경로만 사용
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy warn75-stop80 --output <새_실행> -- \
  .venv/bin/python -u scripts/edge_asr.py tools probe-load \
  --config configs/zipformer_eval.toml \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --duration 480 --output <새_실행>
```

SSH 단절로 열 감시까지 사라지는 것을 피하기 위해 기존 도구를 `nohup`과 새 세션으로 실행하고 stdout/PID/시작 기록을 새 maintenance 폴더에 보존한다. 모델 실행기를 새 프레임워크로 바꾸지 않으며 guard 안의 timeout·주소 공간 제한·열 감시는 그대로 작동한다. 통신이 끊겨도 임의로 두 번째 부하를 시작하지 않고 기존 PID/영수증부터 확인한다.

새 폴링 빈도와 중단선이 과거와 다르므로 과거 75°C 수치와 동일한 통제 조건이라고 하지 않는다. 기존 VAD/음량 정책 선택용 집계기는 75°C 조건의 재현용이다. 새 결과는 우선 별도 냉각 분석에서 다루고 기존 모델 비교표에 자동 병합하지 않는다. 새 조건 통과도 마이크·VAD 정답·Hybrid 동시 부하 합격을 대신하지 않는다.
