# OPT-0 첫 단독 반복·실패 재현 프로토콜 — 2026-11-09

이 계획은 RP5 신규 추론 전에 고정합니다. 실행 ID는 `opt0-pilot-20261109-01`, 현재 기준 구성은 [ONNX 2T 설정](../../configs/optimization/opt0_onnx_2t.toml)입니다. 재사용하는 모델은 기존 INT8 혼합 그래프이며 비양자화 원본 가중치가 아닙니다. [전체 OPT 계획](qwen3_optimization.md).

## 실행 경로 분석

```mermaid
flowchart LR
  WAV[WAV 읽기·해시·float PCM 준비] --> Load[어댑터·모델 로딩]
  Load --> Stream[새 OfflineStream]
  Stream --> Features[accept_waveform: 특징 추출]
  Features --> Conv[Conv frontend]
  Conv --> Encoder[Audio Encoder]
  Encoder --> Prefill[프롬프트·오디오 LLM prefill]
  Prefill --> Decode[토큰 반복 decode·KV 갱신]
  Decode --> Check[텍스트·토큰·native 경고 검사]
```

Python [어댑터](../../src/edge_asr/adapters/qwen3.py)는 `create_stream → set_option(Korean) → accept_waveform → decode_stream → result` 순서로 호출합니다. 준비된 PCM 이후 전체 호출을 측정하며 WAV 읽기·해시·float 변환은 제외합니다. 모델 로딩에는 선택 런타임 import와 인식기 초기화가 포함되지만 앞서 자산을 해시하므로 cold-storage 실험은 아닙니다.

공식 sherpa-onnx v2.0.1의 [OfflineStream](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/offline-stream.cc)은 입력에서 특징을 만들고, [Qwen 구현](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/offline-recognizer-qwen3-asr-impl.cc)의 `Decode`가 Conv frontend·Encoder·GenerateText를 호출합니다. `GenerateText`는 매 전사에 빈 KV cache를 만들고 prefill 후 토큰 단계마다 갱신합니다. 한 전사 내부의 KV 재사용과 다음 음성 청크로 상태를 넘기는 변경은 구분합니다.

[모델 실행 구현](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/offline-qwen3-asr-model.cc)이 ONNX 세션을 실행합니다. 현재 Python의 `decode_stream` 경계 안에 encoder·prefill·token decode가 묶여 있으며 각 단계 시간과 엔진 TTFT는 미계측입니다. 소스의 호출 순서만으로 병목 비율을 추정하지 않습니다. 원본 소스 3개의 SHA-256은 실행 maintenance의 `upstream/sources.json`에 남깁니다.

## 소규모 반복 기준선

기존 KSS 개발 50개를 `(프레임 수, ID)`로 정렬해 1·10·20·30·40·50번째를 선택합니다. 전사를 보거나 오류 크기로 고르지 않습니다.

| ID | 길이(초) |
| --- | --- |
| kss-train-01023 | 0.9260625 |
| kss-train-01022 | 2.2434375 |
| kss-train-01026 | 3.110625 |
| kss-train-01003 | 3.6156875 |
| kss-train-01002 | 4.03025 |
| kss-train-01025 | 5.853 |

고유 음성 6개, 합계 19.7790625초입니다. 모델은 한 번 로드합니다. 첫 파일로 첫 추론 1회와 추가 워밍업 3회를 따로 기록한 다음, 6개를 각 5회 실행합니다. 각 라운드 시작 파일을 한 칸씩 회전합니다. 반복 30회는 고유 음성 30개를 뜻하지 않습니다. 첫 추론 한 건도 여러 길이를 대표하지 않습니다.

성공/실패·정답 문자 커버리지·`ko_cer_v1`·파일별/라운드별 처리 시간·RTF·CPU/RSS·온도를 기록합니다. 반복 파일의 분포와 고유 발화의 정확도를 구분합니다. p95를 계산할 때 n과 분포의 집단을 표시하고 파일별 n=5는 꼬리 지연 추정에 작다고 명시합니다. 이 작은 실행을 장기 열평형이나 전체 한국어 정확도 검증으로 일반화하지 않습니다.

이번 회귀 판정은 **30/30 성공, 문자 커버리지 100%, 선택한 6개에서 기존 2T의 정규화된 전사와 일치, 추가 편집 오류 0개**입니다. 기존 비교의 `qwen3_asr-t2-01` predictions를 입력 ID로 대응합니다. 이후 엔진/양자화의 전체 정확도 허용 기준은 더 큰 집합에서 결과를 보기 전에 고정합니다.

## 56ms 오류 재현

기존 FLEURS `fleurs-ko-validation-0009`의 원 WAV SHA-256과 저장된 두 구간의 PCM SHA-256을 모두 확인합니다. 원 7.44초 발화 중 앞 구간은 `[26560, 106560)`, 잔여 구간은 `[106560, 107456)` 프레임입니다. 잔여 PCM은 896프레임(56ms), SHA-256 `17942f6953cec5b18595f670741e4d5c66cd7e63aa6beb80ed8033629f6b1cde`입니다.

별도 모델 프로세스에서 워밍업 없이 다음 순서로 실행합니다.

1. 56ms만 첫 호출, 같은 입력 다시 호출
2. 기존 앞 5초 구간 → 같은 56ms
3. 56ms에 앞 문맥을 200/500/1000ms씩 붙인 입력
4. 원 발화 전체

새 스트림을 호출마다 만들며 같은 프로세스·모델은 재사용합니다. fresh 모델 첫 호출과 이후 호출을 구분합니다. 원 캠페인의 모든 이전 발화를 재생하는 완전한 상태 재현은 아닙니다. native 경고·raw text/tokens·입력 예산·처리 시간·예외를 보존합니다. 구간별 정답 시각 주석이 없으므로 모든 재현 입력은 정답 없는 진단으로 두며 CER=null입니다. 문맥으로 경고가 사라져도 발화 보존·정확도가 개선됐다고 결론 내리지 않습니다.

진단 프로세스의 exit 0은 계획한 호출·기록 완료를 뜻합니다. 모델 호출 실패 수는 별도 집계하고 `worker.status=completed_with_model_failures`를 유지합니다. 실패 원문을 정상 전사로 채택하거나 짧은 구간을 자동 삭제하지 않습니다.

## 실행·보존

기존 Qwen 별도 환경과 모델을 읽고, 각 작업에 별도 `baseline-01`, `reproduction-01` 경로를 사용합니다. 기본 `thermal-run`의 시작 53°C·중단 75°C·4GiB·외부 300초 제한으로 실행합니다. 시작 조건 미충족·열 중단·실패도 기록하고 새 ID로만 다시 실행합니다.

```bash
# Host/RP5에서 같은 입력 준비. PYTHONPATH는 기존 환경을 설치/변경하지 않는다.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  experiments/qwen3_optimization/prepare_opt0.py \
  --output results/optimization/opt0/opt0-pilot-20261109-01/inputs

# RP5에서 clean main 체크아웃과 별도 Qwen 환경으로 실행하는 형태
.venv-qwen3-onnx-20261106-01/bin/python scripts/edge_asr.py tools thermal-run \
  --output results/optimization/opt0/opt0-pilot-20261109-01/baseline-01 \
  --timeout-s 300 -- \
  .venv-qwen3-onnx-20261106-01/bin/python scripts/edge_asr.py tools opt0-baseline \
  --config configs/optimization/opt0_onnx_2t.toml \
  --manifest results/optimization/opt0/opt0-pilot-20261109-01/inputs/baseline.jsonl \
  --output results/optimization/opt0/opt0-pilot-20261109-01/baseline-01
```

재현 실행은 별도 출력 경로, `inputs/reproduction.jsonl`, `--protocol reproduction --repetitions 1`을 사용합니다. 두 실제 추론은 순차로 실행하고 작업별 시작 조건을 다시 적용합니다. 장기 접속 단절에도 기록이 남도록 독립 실행하며 Host 회수 후 해시를 대조합니다. 기존 환경·패키지·모델·원본·과거 결과는 보존합니다.
