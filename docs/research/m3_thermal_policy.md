# M3 별도 열 정책: 75°C 경고 / 80°C 중단

2026-11-02 사용자가 공식 온도 기준 설명 후 이 조건의 구현·실험을 승인했다. 기존 75°C 결과를 새 기준으로 재판정하지 않고 별도 실행 ID에서 검증한다. 하드웨어/팬 설정·클럭·전압·모델·패키지는 변경하지 않는다.

## 선택과 기록

`thermal-run`의 기본값 `--temperature-policy legacy75`는 과거 재현용으로 유지한다. 이 경우 75°C 중단·폴링 대기 1초와 기존 영수증 형식을 유지한다. 새 실험에만 `--temperature-policy warn75-stop80`을 명시한다.

| 항목 | legacy75 | warn75-stop80 |
| --- | --- | --- |
| 시작 조건 | 53°C 이하·현재 제한 없음 3회 연속 | 동일 |
| 온도 경고 | 별도 기록 없음 | 75°C 이상, 그 이유만으로 중단하지 않음 |
| 온도 중단 | 75°C 이상 관측 | 80°C 이상 관측 |
| 현재 제한·새 이력·필수 계측 누락 | 중단 | 동일하게 중단 |
| 폴링 사이 대기 | 1초 | 0.25초 |
| 외부 제한 | 600초 timeout·4GiB 주소 공간·core 0 | 동일, 실제 PID 확인 필요 |

0.25초는 조회 이후의 대기이며 정확한 표본 간격 보증이 아니다. sysfs/`vcgencmd`/TID 조회 시간이 추가되고 원시 단조 시각으로 실제 간격을 확인한다. 80°C에 절대 도달하지 않는다는 보장도 아니다. 관측 시점의 초과·제한은 그대로 실패로 남긴다. 새 이력이 켜진 실험은 다음 실행 전에 기준선을 재검토하며 무조건 반복하지 않는다.

새 영수증은 `policy.name`, `warn_mc=75000`, `stop_mc=80000`, `interval_s=0.25`를 기록한다. `warnings.jsonl`에 monitor/after 단계의 경고 표본을 보존하고 `warnings.sample_count`와 첫 표본을 기록한다. after 표본도 경고·중단 검사에 포함한다. 경고만 발생하고 나머지 조건을 통과하면 `control_valid=true`가 가능하다. 현재 제한/새 이력/결측을 온도 경고로 낮추지 않는다.

새 조건은 [공식 열 제어 설명](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#frequency-management-and-thermal-control)과 [75°C 최대 팬 제어](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#raspberry-pi-5-fans)를 참고한 프로젝트 측정 정책이며 제조사의 무조건적 안전 보증이 아니다. 팬 곡선 자체는 쓰거나 변경하지 않는다.

## 이번 실제 검사 계획

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
