# M3-0 후속: VAD 실제 검증과 연속 전처리

사용자가 후속 단계 진행을 지시해 소형 Silero VAD만 별도 경로에 추가한다. 사용자는 팬·히트싱크 설치 및 변경 없음을 알려왔다. 이전 Zipformer 2T의 75°C 중단 조건 실패가 해소됐다고 보지 않으며, 이번 실행은 가벼운 VAD와 전처리 검증에 한정한다. 고부하 동시 실행·새 ASR 모델·패키지 갱신·OS 냉각 설정 변경은 하지 않는다.

## 자산과 사전 고정 조건

- 파일: `models/silero-vad-k2-20261029/silero_vad.onnx`.
- [sherpa 공식 배포 안내](https://k2-fsa.github.io/sherpa/onnx/vad/silero-vad.html)의 k2-fsa 16kHz 비-INT8 파일을 사용한다. 추가 변환/양자화 없음. 원본 [Silero 라이선스](https://github.com/snakers4/silero-vad/blob/master/LICENSE)는 MIT이며 로컬에 라이선스와 출처를 보존한다.
- SHA-256: `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`. 공식 배포에서 받은 바이트의 고정 해시이며 공급자의 별도 서명 확인을 뜻하지 않는다.
- 기존 sherpa-onnx/core 2.0.1, NumPy 2.4.6 환경 그대로, VAD CPU 1T, 16kHz/512프레임, threshold 0.5, 최소 음성 100ms, 종료 무음 300ms.
- 평가 입력: 기존 KSS 50개 × 원음/감쇠/감쇠+잡음, 기존 무음 6개·장문 3개, 고정 seed Gaussian 잡음 6개, 짧은 원본 3개를 세 음량 조건으로 이은 연속 입력 1개. **166개/정책**. 동일 참조 목록에 원형과 공통 RMS 후보를 비교한다. 새 최종 독립 평가가 아니다.
- 후보별 빠른 파일 입력은 3/5/8초를 모두 시험한다. 1배속은 사전 고정한 5초 설정으로 실제 짧은 KSS 원음 10개+무음 6개+잡음 6개, 총 22개/정책을 시험한다.
- 음성 파일 전체 누락 0, 무음/합성 잡음 오탐 0을 먼저 확인한다. 둘 다 충족하면 원형 VAD 입력을 우선한다. 실제 단어 누락·초단문·현장 오탐·음소 endpoint 합격은 별도 정렬 검증이 필요하다. 데이터에 맞춰 threshold를 사후 변경하지 않는다.

## 중요한 구현 확인

[설치 버전 v2.0.1의 C++ 구현](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/voice-activity-detector.cc)에서 `max_speech_duration`은 길이를 넘으면 threshold와 종료 무음 기준을 바꾼다. **정확한 최대 길이에서 강제 절단하는 설정이 아니다.** 따라서 실제 반환 구간 길이와 설정 초과 수를 기록한다. 파일 진단의 내부 버퍼는 입력 전체 길이+여유로 잡고 180초 초과 입력을 거부해 버퍼 초과를 예방한다. 이 버퍼 방식은 서비스의 유한 링버퍼 구현을 대신하지 않는다.

실시간 Refine의 3/5/8초 상한은 별도 구간 소유권 처리로 보장해야 한다. 활성 음성의 이미 도착한 프레임을 단위별로 확정하고, 각 소유 범위와 문맥을 나눠야 한다. 완료된 VAD 구간을 사후 분할하는 것만으로는 제때 Refine 작업을 시작했다고 볼 수 없다. 이번에는 해당 Hybrid 실행기를 구현하지 않는다.

`AudioPreprocessor`는 이전 gain/RMS 상태를 100ms 블록 간 유지한다. `final=True`인 마지막 블록만 100ms 미만을 허용하며 명시적 `reset()`으로 새 세션을 시작한다. 기존 파일 함수 `preprocess()`는 호출마다 초기화하는 계약을 유지한다. 과거 커밋의 고정 PCM 해시와 기존 450개 파생 파일 대조로 출력 불변을 확인한다. 실제 연속 스트림에서도 단어·청감이 보존되는지는 별도다.

## 시간·실패 기록

빠른 파일 입력의 `emitted_at_audio_frame`은 알고리즘 오디오 시간축이다. 1배속 모드는 프레임이 도착해야 하는 시각까지 기다리고 `emitted_at_wall_s`를 기록한다. 원본 WAV 끝 기준 오디오/실제 시계 대리 지연을 분리하며 진짜 음소 종료 지연은 null로 둔다. 파일 종료 flush에서만 확정되는 경우 정상 endpoint 지연으로 채우지 않는다.

1배속 VAD 진단은 **동일 스레드 입력기**이며 대기 초과를 따로 기록한다. 독립 수집 큐가 없으므로 드롭·백로그를 0으로 주장하지 않는다. VAD 연산 시간은 입력 대기를 제외하고, 전체 재생 시간·CPU·관측 RSS와 분리한다. 모든 RP5 실행에는 기존 온도/현재 제한 감시 및 timeout/주소 공간 제한을 적용하고 실패 기록을 보존한다.

## 실행 순서

```bash
# Host, 새 자료 생성 및 테스트. 기존 경로는 덮어쓰지 않는다.
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/prepare_vad_cases.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
# main commit/push → RP5 pull --ff-only, 새 모델/새 자료만 복사하고 해시 대조
```

RP5에서 다음 명령의 identity/rms26를 별도 출력 ID로 순서대로 실행한다. 각 실행 폴더는 외부 감시기가 생성한다.

```bash
.venv/bin/python scripts/m30_thermal_run.py --output <새_출력> -- \
  .venv/bin/python -u scripts/m30_vad_probe.py \
  --model models/silero-vad-k2-20261029/silero_vad.onnx \
  --manifest data/evaluation/m3-vad-20261029-01/identity.jsonl --output <새_출력>
# 1배속은 --paced --max-segment 5 및 paced-identity.jsonl / paced-rms26.jsonl 사용
```

새 결과는 `results/preflight/m3-vad-20261029-01`에 보존한다. 과거 `m3-0-20261029-01` 기록은 수정하지 않는다. 최종 보고서에는 실제 VAD 검증 범위와 냉각으로 보류한 동시 실행을 구분한다.
