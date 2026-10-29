# M3-0: 실시간 Hybrid 전제조건

M1/M2 실행 기록과 기본 설정을 그대로 보존한다. 이번 단계는 입력 음량·냉각·VAD/구간 정책의 검증이며 전체 Hybrid 서비스 구현이 아니다. Host 단위 검사와 실제 RP5 추론을 별도로 보고한다.

## 사전 고정 조건

- 기존 KSS 개발 50개, 원래 정답, `ko_cer_v1`, 기존 두 모델 가중치·언어·ITN을 유지한다.
- 음량 실험은 **두 모델 모두 1스레드**, 워밍업 3회로 고정한다. M2의 Zipformer 2T / SenseVoice 4T 선택과 목적이 다른 조건이다. 예전 RTF와 통제 속도 비교를 하지 않는다.
- `identity`, 공통 고정 +12 dB, 인과적 RMS -26 dBFS 후보를 모두 평가한다. 후보를 모델마다 고르지 않는다.
- 개발 입력은 원래 KSS, -24 dB 감쇠, -24 dB 음성 + RMS -55 dBFS 고정 seed Gaussian 잡음이다. 실제 마이크·환경 소음이 아닌 합성 스트레스다.
- 정책 선택 규칙은 **새 clipping 0**, **두 모델 각각 원래 KSS CER 악화 1%p 이하**를 모두 만족하는 후보 중 두 모델 × 세 조건의 CER 평균 최소다. 동률이면 identity를 우선한다. 속도는 보조 관찰이며 사후 임계값 변경으로 선택을 정당화하지 않는다.
- 기존에 결과를 본 FLEURS 100개는 원인 탐색 자료다. 최종 독립 평가로 재사용하지 않는다. 개발 정책을 고정한 뒤 별도 문장 ID의 미사용 자료와 실제 녹음을 준비해야 한다.

RMS 후보는 100ms 현재/이전 5블록 RMS, 최대 +24 dB, -60 dBFS 이하는 증폭하지 않음, gain 증가 시정수 0.3초, 현재 블록 peak 0.98 제한을 사용한다. 발화마다 상태가 초기화되므로 연속 스트림/청감 품질은 미검증이다. 미래 블록을 읽지 않지만 블록 전체가 도착할 때까지의 입력 수집 시간이 필요하다. 무음을 잘라내거나 전사에 금칙어를 적용하지 않는다. 잡음도 증폭할 수 있으며 gain trace와 clipping을 보존한다.

## 냉각 허용 조건

`m30_thermal_run.py`는 53°C 이하·현재 제한 비트 0을 연속 3번 관측한 그 표본에서 시작한다. 실행 중 1초마다 온도, CPU0 클럭, governor, 팬 RPM, 현재/누적 제한을 기록한다. 현재 제한, 새 누적 제한, 필수 계측 누락, **75°C 이상**이면 프로세스를 중단하고 `control_valid=false`로 기록한다. 시작 전 누적 `0xe0000`만으로 현재 제한이 있다고 판단하지 않는다. 과거 이력 비트가 이미 켜져 있으면 표본 사이 짧은 제한은 놓칠 수 있다. 고정 클럭·열평형·실내 온도의 동일성을 보장하지 않는다.

먼저 각 모델 1T를 180초 이상 연속 호출한다. 완료된 50개 단위 반복의 동일 발화 처리 시간을 초반/후반으로 대조한다. 3분 통과가 장시간 서비스 안정성을 입증하지는 않는다. 하나라도 실패하면 뒤의 통제 성능 비교를 중단하고 팬 전원·풍량·히트싱크 접촉·케이스 통풍·주변 온도의 물리적 확인을 요청한다. OS 설정, 팬 곡선, governor, 전압은 변경하지 않는다.

외부 제한은 600초 timeout, 종료 유예 5초, 주소 공간 4 GiB, core 0이다. 주소 공간은 RSS/cgroup 제한이 아니다. 실제 자식 PID의 `/proc/.../limits`를 확인하지 못하면 exit 0이어도 성공 처리하지 않는다. 네이티브 강제 종료 시 `worker.json`이 running으로 남을 수 있으므로 `execution.json`의 실패를 우선한다. TID별 CPU 카운터는 별도 1Hz 로그이며 런타임 역할을 단정하지 않는다.

## 실행

Host에서 테스트 후 main commit/push, RP5에서 clean main `git pull --ff-only`를 수행한다. 기존 `.venv`는 설치·갱신하지 않는다. 새 ID를 사용하고 결과 폴더를 재사용하지 않는다.

```bash
# Host: 원본 체크섬 검사 후 새 WAV와 정책별 manifest 생성
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/prepare_m30_audio.py
# 이 새 data/evaluation/m30-volume-dev 폴더만 RP5 같은 위치로 복사하고 해시 대조
```

`scripts/compare_models.py`의 `configuration` 도우미로 기본 설정의 스레드만 1로 바꾼 파생 TOML을 새 실행 폴더 아래 만든다. 부하 검사는 다음과 같이 외부 감시 안에서 실행한다. `<새_출력>`은 guard가 생성하므로 미리 만들지 않는다.

```bash
.venv/bin/python scripts/m30_thermal_run.py --output <새_출력> -- \
  .venv/bin/python -u scripts/m30_load_probe.py \
  --config <파생_1T.toml> --output <새_출력>
# 두 모델 부하 검사 통과 후에만 공통 평가기 호출
.venv/bin/python scripts/m30_thermal_run.py --output <새_출력> -- \
  .venv/bin/python -u scripts/edge_asr.py evaluate \
  --config <파생_1T.toml> --manifest data/evaluation/m30-volume-dev/dev-identity.jsonl \
  --output-dir <새_출력>/evaluations
```

음량 실행 순서는 identity Zip/Sense → gain12 Sense/Zip → rms26 Zip/Sense로 사전 고정한다. 각 정책 150개는 독립 발화 150개가 아닌 같은 50개 × 3조건이다. batch RTF/CPU는 기존 평가기의 **모델 호출 구간**이며 파생 WAV 생성 시간을 포함하지 않는다. 전처리 계산 시간과 100ms 수집 시간은 별도 계측 후 보고한다. 모델 로딩, warmup, 추론, endpoint/Refine 지연을 합치지 않는다. 미측정 값은 null이다.

## VAD와 발화 구간 후보

공식 [sherpa VAD 문서](https://k2-fsa.github.io/sherpa/onnx/vad/index.html)와 [Silero 배포 안내](https://k2-fsa.github.io/sherpa/onnx/vad/silero-vad.html)는 CPU ONNX VAD를 제공한다. [Silero 공식 저장소](https://github.com/snakers4/silero-vad)는 MIT 라이선스다. RP5 설치된 sherpa 2.0.1에서 VAD API 노출을 확인했지만 가중치 로딩 성공과 동일하지 않다. 별도 VAD 가중치 추가는 사용자의 이번 ‘모델 추가 제외’ 범위를 확인한 후 수행한다.

우선 후보는 Silero 16kHz/512프레임(32ms), 1T, threshold 0.5, 최소 음성 100ms, 종료 무음 300ms, 최대 음성 **3/5/8초**다. threshold·duration은 확정값이 아니다. 각 최대 길이에 동일한 원음·저음량·짧은 실제 발화·순수 무음·잡음·긴 합성 입력을 넣는다. 무음 발화 발생 수와 짧은 발화 완전 누락 수를 따로 기록한다. 단순 RMS 게이트를 VAD 검증의 대체물로 쓰지 않는다.

`scripts/m30_vad_probe.py`는 로컬에 이미 준비된 `--model`과 기존 `--manifest`를 받아 후보마다 인식기를 새로 만들고 파일마다 VAD 상태를 초기화한다. 자동 다운로드·ASR 호출은 없다. 부하 감시 도구 안에서 실행하며, 결과는 `segments.jsonl`에 남긴다. 실제 가중치가 없는 Host 검사는 구간 소유권·문맥·파일 종료 flush·누락 지표의 mock 검사만 수행한다. ARM64 VAD 로딩·정확도·종료 판정 성공으로 간주하지 않는다. VAD 입력의 원본/전처리 선택도 작은 잡음 증폭과 낮은 음량 누락을 대조한 후 고정해야 한다.

KSS에는 실제 음소 끝 정렬이 없으므로 인위적 앞/뒤 무음에 대해 측정 가능한 것은 **원본 WAV 끝 기준 endpoint 대리 지연**이다. 진짜 음성 끝 기준 지연은 null로 두고 일부 자료의 사람 검수 정렬을 별도로 준비한다. 원음 끝에 무음이 있으면 대리 지연이 음수가 될 수 있다. 시간축을 빠르게 입력한 결과는 알고리즘의 입력 오디오 시간축이며, 1배속 실행의 실제 지연과 구분한다.

구간의 소유 범위는 `[start_frame, end_frame)`로 중복 없이 관리하고, Refine 입력에는 별도 200ms 왼쪽 문맥을 허용한다. 소유 구간과 문맥 구간을 분리해 기록한다. 최대 길이에서 강제 분할된 단어는 문맥만으로 해결됐다고 볼 수 없다. 다음 구간 결과를 이어붙이면 중복 단어가 생길 수 있으므로 M3-1에서는 안정적 segment ID와 revision을 사용하고 M3-2에서 경계 정확도·텍스트 병합을 검증한다. 무음은 Refine 호출을 생략할 수 있지만 짧은 음성 누락률 검증 전 배포하지 않는다.

## M3-1 동시 실행 계획

구성은 Zipformer 1T + SenseVoice 1T, Zipformer 1T + SenseVoice 2T, Zipformer 2T + SenseVoice 1T다. 단독 RTF로 동시 성능을 추정하지 않는다. 냉각과 공통 음량·VAD 정책을 고정한 뒤 다음만 구현한다.

1. 단조 시계 기반 1배속 WAV producer 하나, 오디오 프레임 번호와 도착 시각을 보존한다. Zipformer용 bounded queue와 VAD/음성 링버퍼를 분리하고 수집이 Refine 계산을 기다리지 않게 한다.
2. 모델별 독립 프로세스에 한 번 로드한다. Refine는 한 번에 한 구간 처리하고 대기 큐 최대 2개를 초기 후보로 한다. 포화 시 오래된 미착수 구간을 skip하고 이유·프레임 범위를 기록한다. 오디오 드롭과 Refine skip을 별개 집계한다.
3. 구간마다 실제 음성 끝(정렬된 항목만), VAD 확정 시각, enqueue/start/finish 시각을 저장한다. Refine 대기 = start-enqueue, 처리 = finish-start, 종료 후 확정 = finish-실제 음성 끝이다. 정렬 없으면 WAV 끝 대리값으로 명시한다.
4. Streaming 첫 비어 있지 않은 가설은 입력 시작 기준으로 측정하고 실제 단어 인식 지연과 구분한다. 같은 producer의 단독 Streaming을 기준으로 p50/p95 악화, 최대 백로그 초, 드롭 프레임/청크를 대조한다.
5. 각 PID/전체 CPU·RSS, 로딩 RSS, 시스템 가용 메모리, TID CPU, 온도/클럭/현재 제한을 측정한다. 동시 워밍업은 측정에서 제외하고 두 프로세스 ready 후 시작한다. 최대 구간 3/5/8초 중 M3-0에서 살아남은 후보만 사용한다.
6. 구성별 교차 순서 3회, 짧은 검증 후 10분 이상 연속 입력으로 확대한다. 첫 단계 합격 조건은 계측 완결·현재 제한 없음·입력 드롭 0·큐 유계·OOM/hang 없음이다. 허용 자막 지연과 Refine skip 비율은 제품 요구로 정한 뒤 성능 합격을 판단한다. 결과 병합 서비스와 UI는 이번 단계에서 구현하지 않는다.

새 결과는 `results/preflight/<id>`에 보존하고 작은 검토용 요약만 `results/summaries/`에 Git 관리한다. 과거 지표는 새 분석으로 덮어쓰지 않는다.
