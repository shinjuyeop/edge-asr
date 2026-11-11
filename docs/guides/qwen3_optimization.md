# Qwen 최적화 실행 안내

주 기준은 [Qwen 단독1배속 연속 입력](../research/qwen_realtime.md)입니다. [ONNX/GGUF](../../results/summaries/optimization/realtime100-backends-20261110.md)·[VAD 보정](../../results/summaries/optimization/realtime-accuracy-20261110.md)·[갱신2초/4초](../../results/summaries/optimization/realtime-schedule-20261111.md)·[생성 중 표시](../../results/summaries/optimization/realtime-token-20261111.md)를 비교했습니다. 개발 기준은 **Q8·VAD 보정·구간5초·첫1초/이후4초**이며 별도 bridge의 토큰 표시는 선택 옵션입니다. [파일별 batch 실측](../../results/summaries/optimization/fixed100-baseline-20261109.md)은 정확도·처리 비용 진단용 참조이며 [당시 프로토콜](../research/fixed100_baseline.md)과 함께 보존합니다.

## 환경과 설정

저장소 루트에서 기존 환경을 사용합니다.

| 실행 위치·역할 | Python | 설정 |
| --- | --- | --- |
| Host 설정 검사·모의 테스트 | `.venv/bin/python` | 아래 두 설정 모두 검사 가능. ARM 실제 추론은 RP5에서 수행 |
| RP5 ONNX 실제 추론 | `.venv-qwen3-onnx-20261106-01/bin/python` | [onnx.toml](../../configs/onnx.toml) |
| RP5 GGUF 실제 추론 | `.venv/bin/python` | [gguf.toml](../../configs/gguf.toml) |

GGUF는 기존 Python의 ctypes로 별도 llama.cpp/libmtmd C ABI를 호출합니다. 소스·빌드·바이너리는 `runtimes/qwen3-opt/llama-q8-20261109-01/`, 가중치는 `models/qwen3-opt/gguf-q8-20261109-01/`에 있습니다. [연결·빌드 안내](../../native/README.md)를 따르며 기존 환경에 패키지를 갱신하지 않습니다.

아래 명령은 Host에서도 모델 로딩 없이 확인할 수 있습니다.

```bash
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/onnx.toml
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf.toml
.venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 --help
.venv/bin/python scripts/edge_asr.py tools thermal-run --help
```

## 실제 평가 순서

1. Host에서 변경·[모의 테스트](../../tests/README.md)를 마치고 main에 commit/push합니다. RP5는 같은 소스를 pull합니다.
2. [고정100개](../research/fixed100.md)의 원본·WAV·해시를 확인합니다. [`tools prepare-baseline100`](../../src/edge_asr/tools/data/baseline100.py)로 원본을 보존한 채 **새 실행 경로**에 고정 순서 manifest를 준비합니다. 비교 구성의 순서·워밍업 입력은 같습니다.
3. RP5에서 다른 실제 추론과 겹치지 않게 실행합니다. 주 평가는 아래 `tools replay-qwen3`로 모델1회 로딩 → 첫 추론1회 → 추가 워밍업3회 →1배속 연속 입력이며 `thermal-run`의 외부 시간·4GiB 주소 공간 제한 안에서 사용합니다. 보조 batch 참조를 새로 측정할 때는 `tools benchmark-qwen3 --repetitions 1`로100개를 각1회 전사합니다.
4. 원시·해시를 Host에 회수하고 공통 재채점·실패/커버리지·열 상태를 검토한 뒤 [요약 폴더](../../results/summaries/optimization/README.md)와 [현재 상태](../status.md)를 갱신합니다.

고정 순서 목록은 다음 CLI로 준비합니다. output은 새 경로를 사용합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools prepare-baseline100 \
  --output "results/optimization/opt2/<고유ID>/inputs"
```

다음은 **보조 GGUF batch 운영 측정의 호출 형식**입니다. `<...>`를 준비한 동일 순서 manifest와 새 실행 경로로 바꿉니다. 기존 사용자가 허용한 온도 대기 없는 운영 조건을 명시하며 온도·실제 클럭·제한 플래그를 기록합니다. 통제 비교의53°C 시작·75°C 경고/80°C 중단 조건과 전체 비교 순서는 기준선 프로토콜에서 구분합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "results/optimization/opt2/<고유ID>/gguf-2t" \
  --timeout-s 1800 --temperature-policy operational --skip-temperature-admission \
  -- .venv/bin/python scripts/edge_asr.py tools benchmark-qwen3 \
  --config configs/gguf.toml \
  --manifest "<동일순서-manifest.jsonl>" \
  --output "results/optimization/opt2/<고유ID>/gguf-2t" --repetitions 1
```

ONNX 측정은 guard와 자식 명령 모두 표의 ONNX Python·설정을 사용합니다. `evaluate`도 공통 batch 평가를 지원하지만 첫 추론/워밍업을 분리하는 이번 기준선은 `benchmark-qwen3`를 사용합니다. [공통 평가·재채점](evaluation.md), [계측 범위](../reference/benchmark_design.md)를 따릅니다.

## Qwen 단독 1배속 연속 입력

[연속 입력 프로토콜](../research/qwen_realtime.md)은 기존 Qwen·VAD 자산과 Python 환경을 사용합니다. `<manifest>`는 해시가 있는 입력 목록이며 전체를 연결해 파일 경계로 초기화하지 않습니다. `<새ID>`는 새 경로로 지정합니다. 아래는 짧은 GGUF 파일럿의 기본 호출이며 주 평가 조건은 뒤의 ONNX 예시처럼 명시합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "results/optimization/opt3/<새ID>" \
  --timeout-s 600 --temperature-policy operational --skip-temperature-admission \
  -- .venv/bin/python scripts/edge_asr.py tools replay-qwen3 \
  --config configs/gguf.toml --manifest "<manifest>" \
  --output "results/optimization/opt3/<새ID>"
```

입력은100ms·1배속이며2초부터1초 간격으로 부분 전사를 요청하고5초 또는 VAD 끝에서 확정합니다. 첫 자막·부분/확정 원문은 stdout과 `jobs.jsonl`, 입력 드롭·backlog는 `stream/`, 전체 조건·점수·자원은 `worker.json`에 남습니다. 전체100개는 음성만 약15분이므로 외부 제한도 입력 길이·종료 배출을 포함하도록 별도로 지정합니다. 기본600초 예시는 짧은 파일럿용입니다.

**주 평가의 ONNX 출발 구성**도 동일 실행기를 사용합니다. RP5 기존 `.venv`에는 ONNX 어댑터가 요구하는 sherpa-onnx2.0.1과 같은 native 라이브러리·numpy, 공통 VAD용 Python ONNX Runtime이 함께 준비되어 있어 패키지 변경 없이 사용합니다. 기존 ONNX 전용 환경과 모델은 보존합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "results/optimization/opt3/<새ID>/onnx-2t" \
  --timeout-s 1800 --temperature-policy operational --skip-temperature-admission \
  -- .venv/bin/python scripts/edge_asr.py tools replay-qwen3 \
  --config configs/onnx.toml --manifest "<동일순서-manifest.jsonl>" \
  --output "results/optimization/opt3/<새ID>/onnx-2t" \
  --gap-s 0 --vad-threshold 0.3 --silence-policy skip-zero \
  --partial-after-s 1 --partial-interval-s 2 --max-segment-s 5 \
  --drain-timeout-s 30 --queue-capacity 4
```

100개 주 평가에는 파일 사이 **추가 무음0초**를 사용합니다. 원음의 무음은 제거하지 않습니다. 이후 GGUF는 같은 명령에서 설정을 `configs/gguf.toml`로 바꾸고 새 출력 경로를 사용합니다. 기존 추가 무음1초 Q8 기록은 별도 조건이며 새 ONNX와 직접 우열을 정하지 않습니다.

[ONNX 출발 실측](../../results/summaries/optimization/realtime100-onnx-20261110.md)은 전체 입력 완료·전사1회 실패·VAD 누락1개를 포함한 기록입니다. 모델·조건을 자동 변경하지 않고 실패까지 채점했으며 실사용 통과 구성으로 취급하지 않습니다. 이후 엔진 비교에서 최종 구간 좌표·PCM 해시·첫 자막 커버리지를 함께 확인합니다.

다음 개발 구성은 위 명령에서 `--config configs/gguf.toml`과 새 출력 경로를 사용하고 아래 조건을 명시합니다. `--vad-preprocess rms26-floor80`은 **VAD 입력만** 보정하며 ASR에는 원음을 전달합니다. CLI 기본값은 여전히 `identity`입니다.

```bash
--gap-s 0 --vad-threshold 0.3 --silence-policy skip-zero \
--vad-preprocess rms26-floor80 --vad-max-speech-s 5 --max-segment-s 5 \
--partial-after-s 1 --partial-interval-s 4 --drain-timeout-s 30 --queue-capacity 4
```

2026-11-11 같은100개 새 대조에서2초→4초 갱신은 최종 전사·CER11.12%를 유지하며 첫 자막 p50/p95를6.92/9.34→4.01/5.49초로 줄였습니다. 부분 자막 갱신은 줄어드는 절충이 있으며 CLI 기본값은 유지합니다. [조건·지연·갱신 빈도](../../results/summaries/optimization/realtime-schedule-20261111.md). 저지연 실사용 채택은 보류합니다.

이전 보정 대조의 CER13.23→11.12%와 구간8초 복합 구성의8.26%는 [정확도 회복 기록](../../results/summaries/optimization/realtime-accuracy-20261110.md)에 보존합니다. `--vad-max-speech-s 30`은 VAD 판정 강화 시점만 바꾸며 ASR 상한과는 독립입니다.8초 복합 구성은 첫 자막 p95가15.09초로 늘어 정확도 참고로 둡니다.

대조는 같은 명령에 `--no-partials`를 추가해 구간/PCM은 유지하고 부분 호출만 끕니다. 현재 기본 `--silence-policy skip-zero`는 완전히0인 PCM의 ASR 호출을 생략하고 구간·해시·이유를 따로 기록합니다. 낮은 진폭 음성을 임계값으로 제거하지 않습니다. 이전 동작은 `--silence-policy preserve`로 재현합니다.

전체100개 입력에는 외부 `--timeout-s 1800`을 사용합니다. 이전 추가 무음1초 조건은 [별도 실측 보고서](../../results/summaries/optimization/realtime100-q8-20261110.md)에 보존합니다. 당시 고정12dB 증폭은 오류 증가로 폐기했고 소스 `be32cda`로 재현합니다. 현재 RMS 보정과 구분합니다. 양자화 후보는 `--config configs/quantization/q6_k.toml` 등으로 선택하며 대응하는 GGUF·자산 명세가 별도로 준비되어 있어야 합니다. VAD·부분 전사 조건은 비교 모델 사이에서 동일하게 지정합니다.

## 생성 중 텍스트 배포

`--token-stream`은 `token_callback_v1`이 기록된 **별도 GGUF bridge 빌드**가 필요합니다. 기존 `configs/gguf.toml`은 예전 라이브러리를 유지하므로 새 실험 TOML의 `runtime_library`·`runtime_manifest`를 새 빌드 경로로 지정합니다. [빌드 안내](../../native/README.md), [검증 전 고정 조건](../research/qwen_realtime.md).

같은1배속 명령에서 새 TOML·새 실행 ID를 사용하고 `--token-stream`을 추가하면 생성 중 UTF-8 문자열이 `생성중 partial/final`로 표시됩니다. 생성 중 final도 임시 가설이며 실제 확정은 종료 `result`입니다. 기존 `first_caption_from_track_start_s`는 완성 문자열 반환 지표로 유지하고, `first_visible_caption_from_track_start_s`·`token_timings`·`engine_ttft_s`를 구분해 확인합니다. engine TTFT는 native 호출 시작→첫 비EOG 생성 토큰이고 음성 처리/prefill을 포함하며 큐·Python 표시 시각과 다릅니다. 임시 가설은 최종 CER에 더하지 않고 이후 실패도 보존합니다.

RP5의 `realtime-token-20261111-01`에 새 bridge와 실험 TOML을 준비했습니다. 아래는 같은 개발 조건으로 **새 실행 경로**에 실행하는 형식입니다. 다른 환경에서는 위 빌드 안내대로 새 bridge를 만들고 TOML의 library/manifest 경로를 맞춥니다. 기존 기본 설정은 유지합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "results/optimization/opt3/<새ID>" \
  --timeout-s 1800 --temperature-policy operational --skip-temperature-admission \
  -- .venv/bin/python scripts/edge_asr.py tools replay-qwen3 \
  --config results/optimization/opt3/realtime-token-20261111-01/gguf-token.toml \
  --manifest "<동일순서-manifest.jsonl>" --output "results/optimization/opt3/<새ID>" \
  --gap-s 0 --vad-threshold 0.3 --silence-policy skip-zero \
  --vad-preprocess rms26-floor80 --vad-max-speech-s 5 --max-segment-s 5 \
  --partial-after-s 1 --partial-interval-s 4 --drain-timeout-s 30 --queue-capacity 4 \
  --token-stream
```

[같은100개 대조](../../results/summaries/optimization/realtime-token-20261111.md)에서 첫 표시 p50/p95는3.94/5.49→3.73/5.19초·CER11.12% 동일이었습니다. 전체 추론 시간은2.2% 늘고 최종 반환 p95도4.07→4.16초로 늘었습니다. 표시 개선으로 해석하며 임시 자막 품질·마이크·장시간 운영은 후속입니다.

이후 토큰 표시는 `hold_replayed_prefix_v1`을 사용합니다. 같은 구간의 이미 보인 원문 접두부를 새 호출이 다시 생성하는 동안 이전 문자열을 유지하고, 확장되거나 내용이 달라지면 표시합니다. 옵션을 추가하지 않고 기존 `--token-stream` 안의 표시 동작만 보완합니다. `worker.json`의 `token_display_policy`·`caption_stability`, 원시 토큰의 `prefix_replay_suppressed`로 확인합니다. 문자 철회는 정규화 공통 접두부 뒤 제거된 문자수이고 편집 거리나 부분 CER가 아닙니다. 4자 보존 시각도 자기 최종 전사를 기준으로 한 후향 평가입니다. [고정 검증 조건](../research/qwen_realtime.md).

[접두부 보류 검증](../../results/summaries/optimization/realtime-stability-20261111.md)에서 같은 로그의 철회 문자577→161자·첫 표시/최종 결과 동일을 확인했고, 새 RP5 100개에서도 재표시 철회0·전체163자·CER11.12%·실패/드롭0이었습니다. 실제 내용 변경과 실패 처리는 유지합니다. 새 대조 실행을 추가하지 않아 과거와의 시간 차이를 엔진 가속으로 해석하지 않습니다.

## 이전 실험 재현

KSS 등 이전 실험 설정은 [설정 색인](../../configs/README.md)에 보존합니다. 소규모 반복·56ms 진단은 [OPT-0](../research/opt0_pilot.md), 별도 C API/ORT 호출 계측은 [OPT-1](../research/opt1_profiling.md), 최초 GGUF/KSS 비교는 [OPT-2](../research/opt2_gguf.md)를 따릅니다. `opt0-baseline`은 현재 `benchmark-qwen3`와 같은 실행기 이름입니다. 프로파일 계측 실행과 일반 성능 측정은 구분합니다.
