# M3 후속: 지속 음성 VAD와 냉각 진단

2026-10-29 Host 구현 단계다. 기존 네이티브 VAD와 측정값을 유지하고, 지속 음성에서 내부 PCM 축적을 피하는 **선택적 실험 경로**를 추가했다. 실제 ARM64 모델 로딩·구간 동등성·성능은 아직 검증하지 않았다. 이번에는 RP5 SSH에 연결할 수 없어 모델 실행과 RP5 pull을 수행하지 못했다.

## 구현 근거와 범위

기존 sherpa-onnx 2.0.1의 [C++ wrapper](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/voice-activity-detector.cc)는 활성 음성 파형을 보관하고 `current_segment` 조회 때 파형을 복사한다. [Python 바인딩](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/python/csrc/voice-activity-detector.cc)에는 확률만 반환하는 `Compute`가 노출되지 않는다. 따라서 기존 경로의 31초 버퍼/30초 활성 예산을 단순히 늘리거나 활성 중 `flush/reset`을 호출하지 않는다.

`vad_probability.py`는 기존 k2 Silero v4 파일을 **Python ONNX Runtime**으로 읽는다. 알려진 SHA-256과 `x,h,c → prob,new_h,new_c` 서명을 검사한다. 파일·모델·양자화를 바꾸지 않는다. h/c 상태 각 `(2,1,64)` float32, 합계 1,024바이트를 창 사이에 이어간다. CPU provider, intra/inter 1T, 순차 실행이다. 이 값은 신경망 상태 크기이며 모델 세션·입력 WAV·프로세스 RSS가 아니다.

확률 기반 구간기는 파형 대신 프레임 좌표, 시작/종료 대기 상태, 완료 구간 1개를 유지한다. 완료 큐를 소비하지 않고 다음 창을 주면 명확히 실패한다. 실제 PCM은 기존 `SegmentPipeline`의 유한 링버퍼가 보관한다. 3/5/8초 소유 구간과 최대 200ms 왼쪽 문맥을 그대로 사용하고, 강제 구간마다 신경망을 reset하거나 입력을 재전송하지 않는다. flush는 EOF에서만 한다.

[설치 버전의 구간 판정](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/silero-vad-model.cc)을 참고해 threshold 0.5, 최소 음성 100ms, 종료 무음 300ms, 음성 유지 임계값 `threshold - 0.15`를 사용한다. 이전 가상 버퍼가 5초보다 길면 threshold 0.9/종료 무음 100ms로 바뀌는 soft limit도 유지한다. 활성 스냅샷은 원래 wrapper처럼 `tail-1`까지 노출하고 자연 종료/EOF 구간 끝은 별도로 계산한다. Python/C++ 부동소수점 임계값과 ORT 버전 차이를 포함한 동등성은 실제 비교 대상이다.

기본 백엔드는 여전히 `native`다. `SegmentPipeline`의 활성 예산을 해제하려면 파형 축적 0을 선언하는 이 경로를 명시해야 한다. 기존 네이티브 객체에는 예산 해제를 허용하지 않는다. 무중단 ASR 서비스 전체의 메모리 상한이나 정확도를 검증했다는 뜻은 아니다.

## 계측 및 결과 분리

기존 `probe-annotated-vad`에 `--backend bounded`를 선택적으로 추가했다. 공통 전처리·PCM 해시 검증·자원 구간·워밍업·발화별 reset·실패 기록·예비 주석 채점은 재사용한다. 기본 5초 소유 구간, 빠른 파일 입력 방식이다. 사람 검수 정답과 실제 endpoint/첫 자막/Refine 지연은 만들지 않는다.

- `worker.json`: `backend`, Python ORT 버전/provider/스레드/가중치 해시, 로딩 비용을 기록한다. bounded 설정의 native buffer/활성 예산/soft limit 필드는 null이며 별도 `probability_soft_limit_s=5`를 기록한다.
- `samples.jsonl`: 기존 구간·공통 PCM·자원과 함께 `bounded_vad`의 내부 파형 보관 0, 큐 피크, 활성 시간축 길이를 기록한다. 시간축이 길어져도 그 길이의 파형을 저장했다는 뜻은 아니다.
- native와 Python ORT는 서로 다른 런타임이다. 기존 ARM64 기록의 native ORT 1.28.2와 Python 패키지 1.31.1을 같은 버전으로 취급하지 않는다. 새 실행에서는 실제 환경을 다시 기록한다.
- 기존 `summarize-annotated-vad`는 bounded 결과를 네이티브 비교표에 합치지 않고 거부한다. 과거 백엔드 필드 없는 원시는 native로 읽어 호환한다.
- 공개 개발 80개만 실행하고 보류 화자 8명/18개는 워밍업에도 사용하지 않는다. 미확정 청취 메모 5개는 모두 `판단 어려움`으로 보존하며 정답·주석을 바꾸지 않는다.

`pyproject.toml`의 `vad-probability` extra는 새 환경 재현용이다. **현재 RP5 .venv에 재설치하지 않는다.** 기존 환경에 Python ORT가 없는 경우 실행을 중단해 보고하고 자동 설치하지 않는다. Host 테스트는 모의 NumPy/ORT와 합성 확률만 사용한다.

## 연결 복구 후 실제 검증 순서

1. Host main push 후 RP5가 clean main인지 확인하고 `git pull --ff-only`한다. 기존 가중치·데이터·환경·원시 결과의 보존 스냅샷을 남긴다. 패키지와 OS 설정을 변경하지 않는다.
2. 아래 냉각 센서 조회를 먼저 한다. 이 조회가 성공해도 기존 2T 고부하 냉각 실패를 해소한 것으로 간주하지 않는다. 새 실험은 가벼운 VAD 범위다.
3. 기존 고정 주석과 가중치를 사용한다. `results/preflight/m3-bounded-vad-20261029-01/identity`와 `.../rms26`가 모두 없는지 확인하고, 각 정책을 순서대로 실행한다. 이미 있는 ID는 재사용하지 않는다. 아래 경로 변수는 새 ID의 예다.

```bash
# RP5 /home/deepet/projects/edge-asr에서만 실행
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools inspect-cooling \
  --live --output results/preflight/cooling-live-20261029-01.json

bounded_out=results/preflight/m3-bounded-vad-20261029-01/identity
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "$bounded_out" -- \
  .venv/bin/python -u scripts/edge_asr.py tools probe-annotated-vad \
  --annotations data/review/m3-vad-annotations-20261029-01/annotations.jsonl \
  --policy identity --mode provisional --backend bounded --output "$bounded_out"
# rms26는 별도 출력 폴더와 --policy rms26로 순차 실행
```

4. 외부 열/timeout/주소 공간 제한이 유효한지, 80개+워밍업이 완료됐는지, PCM/가중치/주석 해시가 같은지 먼저 확인한다. 실패는 별도 보존한다. 기존 native 결과와 표본별 구간 경계·강제/자연/EOF 이유·예비 FN/FP를 대조한다. 차이가 있으면 그대로 보고하고 기본 백엔드를 바꾸지 않는다. 필요시 같은 새 소스에서 native를 별도 ID로 재실행한다.
5. 실제 >30초 연속 활성과 RSS 추이를 따로 확인한다. 실음성 연결 자료가 VAD에서 중간 종료되면 장시간 활성 통과로 인정하지 않는다. 현재 Host의 지속 확률 모의 검사는 구조 검사이며 실제 신경망 추론 통과 기록이 아니다.
6. 3/5/8초 소유권·동일 PCM·중복 없는 경계·누락 위험을 실제 경로에서도 확인한 뒤 정책 채택 여부를 결정한다. 마이크·독립 입력 큐·Hybrid 동시 부하 검증은 이후 단계다. 단독 VAD RTF로 이 성능을 추정하지 않는다.

## 냉각 진단

`inspect-cooling --run <기존 실행>`은 기존 `execution.json`·`thermal.jsonl`을 수정하지 않고 해시와 재분석을 새 JSON에 저장한다. `--live`는 명령이 실행된 기기의 thermal zones/trip points, cooling devices, 모든 노출 fan 센서, CPU governor/주파수와 현재 제한을 읽는다. Host에서 실행한 센서는 RP5로 표시하지 않는다. 팬 센서 결측은 0RPM으로 바꾸지 않는다. 명령은 부하 실행, 팬/PWM·클럭·OS 설정 쓰기를 하지 않는다.

[Raspberry Pi 공식 `get_throttled` 정의](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled)에 따라 하위 비트 0~3은 현재 저전압·클럭 제한·throttle·soft 온도 제한, 상위 16~19는 발생 이력이다. 프로젝트의 75°C 정지 기준은 이 비트와 별개다. 과거 `0xe0000` 이력만으로 해당 부하 중 실제 제한을 단정하지 않는다. 1Hz 표본 사이의 순간 제한도 부재를 보장하지 못한다.

[과거 냉각 재분석](../../results/summaries/m3-cooling-audit-20261029.json)에서 팬이 회전하고 RPM이 증가한 사실은 확인했다. Zipformer 2T는 약 14.09초 후 76°C 표본에서 중단했으며 직후 표본은 77.1°C였다. 현재 제한 비트는 관측 표본에서 0이고 새 이력은 없었다. SenseVoice 1T의 기존 480표본은 최대 73.8°C였다. 팬 회전만으로 방열판 접촉·통풍·동시 부하 냉각 합격을 판단하지 않는다. 현재 장치 상태는 SSH 연결 실패로 미확인이다.
