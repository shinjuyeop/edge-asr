# Qwen 최적화 실행 안내

주 기준은 [Qwen 단독1배속 연속 입력](../research/qwen_realtime.md)입니다. 모델·스레드·캐시·native 경로는 [공유 개발 설정](../../configs/gguf_realtime.toml), 연속 입력 옵션은 [대표 명령](#현재-개발용-대표-명령)을 사용합니다. 현재 채택 근거와 최신 결과는 [현재 상태](../status.md), 선택이 바뀐 과정은 [ASR 최적화 과정](../research/optimization_history.md)에 정리했습니다. 아래 이전 실험의 설정·판정은 당시 기록입니다.

## 환경과 설정

저장소 루트에서 기존 환경을 사용합니다. 실제 확인한 OS·Python·패키지·native 빌드는 [환경 기록](../reference/environment.md)에 있습니다.

| 실행 위치·역할 | Python | 설정 |
| --- | --- | --- |
| Host 설정 검사·모의 테스트 | `.venv/bin/python` | 공유 설정과 보존 기준선 모두 검사 가능. ARM 실제 추론은 RP5에서 수행 |
| RP5 ONNX 실제 추론 | `.venv-qwen3-onnx-20261106-01/bin/python` | [onnx.toml](../../configs/onnx.toml) |
| RP5 GGUF 실제 추론 | `.venv/bin/python` | [gguf_realtime.toml](../../configs/gguf_realtime.toml)은 현재 개발용, [gguf.toml](../../configs/gguf.toml)은 2T 보존 기준선 |

GGUF는 기존 Python의 ctypes로 별도 llama.cpp/libmtmd C ABI를 호출합니다. 외부 엔진 소스·빌드는 `runtimes/qwen3-opt/llama-q8-20261109-01/`에 있습니다. 현재 공유 설정의 bridge는 `runtimes/qwen3-opt/prefix-20261112-01/bridge/`이며 토큰 표시·스레드 분리·고정 text prefix KV를 지원합니다. 실행할 모델·라이브러리·빌드 명세는 공유 설정의 `model.path`·`model.runtime_library`·`model.runtime_manifest`를 기준으로 확인합니다.

`runtimes/qwen3-opt/split-20261111-01/bridge/`는 이전 스레드 분리 실험용으로 보존합니다. [환경 기록](../reference/environment.md)의 경로·해시는 2026-11-11 확인 당시의 스냅샷입니다. 새 빌드는 [연결·빌드 안내](../../native/README.md)를 따르며 기존 환경에 패키지를 갱신하지 않습니다.

아래 명령은 Host에서도 모델 로딩 없이 확인할 수 있습니다.

```bash
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/gguf_realtime.toml
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

기본2T 재현은 위 명령에서 `--config configs/gguf.toml`과 새 출력 경로를 사용하고 아래 조건을 명시합니다. 현재 개발 구성은 [대표 명령](#현재-개발용-대표-명령)의 공유 TOML을 사용합니다. `--vad-preprocess rms26-floor80`은 **VAD 입력만** 보정하며 ASR에는 원음을 전달합니다. CLI 기본값은 여전히 `identity`입니다.

```bash
--gap-s 0 --vad-threshold 0.3 --silence-policy skip-zero \
--vad-preprocess rms26-floor80 --vad-max-speech-s 5 --max-segment-s 5 \
--partial-after-s 1 --partial-interval-s 4 --drain-timeout-s 30 --queue-capacity 4
```

2026-11-11 같은100개 새 대조에서2초→4초 갱신은 최종 전사·CER11.12%를 유지하며 첫 자막 p50/p95를6.92/9.34→4.01/5.49초로 줄였습니다. 부분 자막 갱신은 줄어드는 절충이 있으며 CLI 기본값은 유지합니다. [조건·지연·갱신 빈도](../../results/summaries/optimization/realtime-schedule-20261111.md). 저지연 실사용 채택은 보류합니다.

이전 보정 대조의 CER13.23→11.12%와 구간8초 복합 구성의8.26%는 [정확도 회복 기록](../../results/summaries/optimization/realtime-accuracy-20261110.md)에 보존합니다. `--vad-max-speech-s 30`은 VAD 판정 강화 시점만 바꾸며 ASR 상한과는 독립입니다.8초 복합 구성은 첫 자막 p95가15.09초로 늘어 정확도 참고로 둡니다.

대조는 같은 명령에 `--no-partials`를 추가해 구간/PCM은 유지하고 부분 호출만 끕니다. 현재 기본 `--silence-policy skip-zero`는 완전히0인 PCM의 ASR 호출을 생략하고 구간·해시·이유를 따로 기록합니다. 낮은 진폭 음성을 임계값으로 제거하지 않습니다. 이전 동작은 `--silence-policy preserve`로 재현합니다.

전체100개 입력에는 외부 `--timeout-s 1800`을 사용합니다. 이전 추가 무음1초 조건은 [별도 실측 보고서](../../results/summaries/optimization/realtime100-q8-20261110.md)에 보존합니다. 당시 고정12dB 증폭은 오류 증가로 폐기했고 소스 `be32cda`로 재현합니다. 현재 RMS 보정과 구분합니다. 양자화 후보는 `--config configs/quantization/q6_k.toml` 등으로 선택하며 대응하는 GGUF·자산 명세가 별도로 준비되어 있어야 합니다. VAD·부분 전사 조건은 비교 모델 사이에서 동일하게 지정합니다.

현재 가변 조건의 Q4 비교에는 [q4_k_m_realtime.toml](../../configs/quantization/q4_k_m_realtime.toml)을 사용합니다. 기존 `q4_k_m.toml`은 예전2T/bridge 재현용으로 보존합니다. 새 설정은 text Q4_K_M·audio Q8_0·현재 prefix bridge·생성2T/음성3T이며, 입력/VAD/가변 요청 조건을 Q8과 같게 지정합니다. [2026-11-13 선별 조건](../research/qwen_realtime.md#q4-음성비용-가변-대조--2026-11-13-사전-조건).

[6개 선별 결과](../../results/summaries/optimization/realtime-q4-20261113.md)는 메모리/최종 반환 이득과 최종 오류2개 증가를 확인했습니다.100개 확대·기본 전환은 하지 않았으며 다음 저지연 개발 비교는 Q8 음성비용 가변을 사용합니다.

text와 audio를 함께 낮추는 별도 비교는 [q4_both_realtime.toml](../../configs/quantization/q4_both_realtime.toml)을 사용합니다. text Q4_K_M·audio Q4_0이며 명세 v2와 `audio_quantization`을 명시합니다. 기존 text-only Q4/Q8 설정·명세는 유지합니다. [사전 조건](../research/qwen_realtime.md#textaudio-모두-q4-대조--2026-11-13-사전-조건)을 따르며 파일 크기 감소를 속도/정확도 개선으로 가정하지 않습니다. [6개 실측](../../results/summaries/optimization/realtime-both-q4-20261113.md)에서 메모리/최종 반환은 줄었지만 CER가 늘고 encoder 가속은 없어 Q8 가변 기준을 유지했습니다.

Q4 안의 후속 prefill 비교는 [q4_prefill_realtime.toml](../../configs/quantization/q4_prefill_realtime.toml)을 사용합니다. 같은 text/audio Q4·기존 repack ON·생성2T/음성prefill4T이며 가변 조건은 기존대로 별도 지정합니다. [6개 선별 결과](../../results/summaries/optimization/prefill-q4-20261113.md)는 소폭 지연 감소와 CPU 시간 증가를 함께 기록합니다. 기존 Q4 3T와 Q8 공유 기본은 보존합니다.

## 첫 후속 갱신 선별

부분 요청의 첫 후속 갱신만 앞당기는 선택 실험은 기존 명령에 `--partial-followup-s 2`를 추가합니다. 첫1초 요청 뒤2초 후 한 번 더 요청하고 이후에는 `--partial-interval-s 4`를 따릅니다. 각 VAD/ASR 구간마다 적용되며, 생략하면 기존 간격을 유지합니다. 최종 구간·PCM·모델은 같고 부분 호출·큐 부담은 실측으로 확인합니다. 공유 개발 설정과 청취 기본 구성은 변경하지 않습니다.

## 생성 중 텍스트 배포

`--token-stream`은 `token_callback_v1`이 기록된 **별도 GGUF bridge 빌드**가 필요합니다. 기존 `configs/gguf.toml`은 예전 라이브러리를 유지하므로 새 실험 TOML의 `runtime_library`·`runtime_manifest`를 새 빌드 경로로 지정합니다. [빌드 안내](../../native/README.md), [검증 전 고정 조건](../research/qwen_realtime.md).

같은1배속 명령에서 새 TOML·새 실행 ID를 사용하고 `--token-stream`을 추가하면 생성 중 UTF-8 문자열이 `생성중 partial/final`로 표시됩니다. 생성 중 final도 임시 가설이며 실제 확정은 종료 `result`입니다. 기존 `first_caption_from_track_start_s`는 완성 문자열 반환 지표로 유지하고, `first_visible_caption_from_track_start_s`·`token_timings`·`engine_ttft_s`를 구분해 확인합니다. engine TTFT는 native 호출 시작→첫 비EOG 생성 토큰이고 음성 처리/prefill을 포함하며 큐·Python 표시 시각과 다릅니다. 임시 가설은 최종 CER에 더하지 않고 이후 실패도 보존합니다.

## 현재 개발용 대표 명령

아래는 [공유 개발 설정](../../configs/gguf_realtime.toml)으로 **새 실행 경로**에 실행하는 형식입니다. RP5의 모델·bridge는 [환경과 설정](#환경과-설정)을 확인합니다. 다른 환경에서는 빌드 안내대로 필요한 선택 기능을 지원하는 bridge를 만들고 별도 TOML의 library/manifest 경로를 맞춥니다. TOML은 모델·평가 계약만 담으므로 아래 VAD·부분 갱신·토큰 표시 옵션도 함께 지정합니다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "results/optimization/opt3/<새ID>" \
  --timeout-s 1800 --temperature-policy operational --skip-temperature-admission \
  -- .venv/bin/python scripts/edge_asr.py tools replay-qwen3 \
  --config configs/gguf_realtime.toml \
  --manifest "<동일순서-manifest.jsonl>" --output "results/optimization/opt3/<새ID>" \
  --vad-model models/silero-vad-k2-20261029/silero_vad.onnx \
  --gap-s 0 --vad-threshold 0.3 --silence-policy skip-zero \
  --vad-preprocess rms26-floor80 --vad-max-speech-s 5 --max-segment-s 5 \
  --partial-after-s 1 --partial-interval-s 4 --drain-timeout-s 30 --queue-capacity 4 \
  --token-stream
```

[같은100개 대조](../../results/summaries/optimization/realtime-token-20261111.md)에서 첫 표시 p50/p95는3.94/5.49→3.73/5.19초·CER11.12% 동일이었습니다. 전체 추론 시간은2.2% 늘고 최종 반환 p95도4.07→4.16초로 늘었습니다. 표시 개선으로 해석하며 임시 자막 품질·마이크·장시간 운영은 후속입니다.

이후 토큰 표시는 `hold_replayed_prefix_v1`을 사용합니다. 같은 구간의 이미 보인 원문 접두부를 새 호출이 다시 생성하는 동안 이전 문자열을 유지하고, 확장되거나 내용이 달라지면 표시합니다. 옵션을 추가하지 않고 기존 `--token-stream` 안의 표시 동작만 보완합니다. `worker.json`의 `token_display_policy`·`caption_stability`, 원시 토큰의 `prefix_replay_suppressed`로 확인합니다. 문자 철회는 정규화 공통 접두부 뒤 제거된 문자수이고 편집 거리나 부분 CER가 아닙니다. 4자 보존 시각도 자기 최종 전사를 기준으로 한 후향 평가입니다. [고정 검증 조건](../research/qwen_realtime.md).

[접두부 보류 검증](../../results/summaries/optimization/realtime-stability-20261111.md)에서 같은 로그의 철회 문자577→161자·첫 표시/최종 결과 동일을 확인했고, 새 RP5 100개에서도 재표시 철회0·전체163자·CER11.12%·실패/드롭0이었습니다. 실제 내용 변경과 실패 처리는 유지합니다. 새 대조 실행을 추가하지 않아 과거와의 시간 차이를 엔진 가속으로 해석하지 않습니다.

[이전 스레드 대조](../../results/summaries/optimization/realtime-threads-20261111.md)는 같은 소스의100개 새2T→3T를 비교했습니다. 최종210구간 PCM·전사·토큰 ID·CER11.12%를 유지하며 최종 추론 합계8.9%·전체 합계8.3%가 줄었습니다. 첫 표시 p95는5.20→4.56초·고정 파일 끝 반환 p95는4.22→3.72초였습니다. 당시 모두3T를 선택했지만 decode 비용과 최고 온도는 늘었습니다. 이전 설정·결과는 그대로입니다.

## 생성·음성 처리 스레드 분리 실험

GGUF의 선택 항목 `model.num_threads_batch`는 여러 토큰 prefill과 오디오 인코딩에 적용됩니다. `num_threads`는 토큰 생성용이며 `num_threads_batch`를 생략하면 기존처럼 모두 같은 값을 씁니다. 명시할 때는 `split_threads_v1`이 기록된 별도 bridge와 새 TOML이 필요합니다. 기존2T·3T 파일과 토큰 bridge는 그대로 보존합니다. [빌드 안내](../../native/README.md), [실측 전 조건](../research/qwen_realtime.md).

```toml
# 새 실험 TOML의 [model] 안에 지정. 나머지 Q8·자산·새 bridge 경로 유지.
num_threads = 2
num_threads_batch = 3
```

Python 어댑터는 생성/prefill의 native getter 값과 mtmd 초기화 전달값 `audio_threads`가 요청과 같은지 확인합니다. 불일치·기능/심볼 누락은 실패로 기록하며 기존 값으로 조용히 대체하지 않습니다. 입력·분할·토큰 표시·KV 초기화는 유지합니다.

[6개 분리 검토](../../results/summaries/optimization/realtime-split-20261111.md)는 최종 처리 합계 개선3.88%·2.82%로 반복3% 선별 조건에 미달해 해당 실행은100개 없이 종료했습니다. 이 판정은 보존합니다. 이후 [별도100개 추가 대조](../../results/summaries/optimization/realtime-split100-20261111.md)에서 새3/3→2/3은 최종 합계3.68%·decode7.76% 감소·CER11.12% 동일·실패/드롭/미출력0으로 사전 조건16개를 통과했습니다. 당시2/3을 개발 기준으로 선택했으며 순차1쌍·열 제한을 포함한 운영 관측입니다. 통계적 유의성·실사용 채택과 구분합니다.

추가 [음성/prefill3T/4T 대조](../../results/summaries/optimization/realtime-prefill-20261112.md)는 생성2T를 고정하고 같은6개를2/3→2/4→2/3으로 실행했습니다. 전사·실패/드롭/미출력은 같았고, 후보의 음성/prefill·TTFT는 줄었지만 앞control 대비 전체 추론 합계가0.069초(0.17%) 늘었습니다. 당시2/3을 유지하며100개는 실행하지 않았습니다. 운영 변동을 포함한 선별 결과로4T의 효과가 없다고 단정하지 않습니다.

[반복 처리·캐시 분석](../../results/summaries/optimization/realtime-cache-audit-20261112.md)은 기존2/3 로그를 재집계한 기록입니다. 당시 KV 초기화 비중은0.37%였고 고정 text prefix 재사용을 검증 후보로 정했습니다. 후속 구현·검증은 아래 선택 실험에 연결합니다. 입력이 늘어나는 호출 사이의 음성 KV 재사용은 미구현입니다.

## 고정 text prefix KV 선택 실험

선택 항목 `model.cache_text_prefix`의 현재 값과 빌드는 [공유 개발 설정](../../configs/gguf_realtime.toml)을 확인합니다. 별도 실험 TOML에서 `cache_text_prefix=false`/`true`로 같은 빌드의 끔/켬 대조를 재현하며, 기존 항목 생략 설정과 라이브러리도 보존합니다. 명시한 항목이 기존 빌드에서 지원되지 않으면 오류로 기록합니다. 빌드 방법은 [native 안내](../../native/README.md), 입력·판정은 [사전 조건](../research/qwen_realtime.md#고정-text-prefix-kv-대조--2026-11-12-사전-조건)을 따릅니다.

같은 대표 연속 명령에서 TOML과 새 출력 경로를 지정합니다. `worker.json`의 첫 추론/워밍업·호출별 `native_result.prefix_cache`, `prefix_eval_s`·`suffix_eval_s`·`audio_encode_s`·`audio_embedding_eval_s`를 확인합니다. 전체 음성·음성 뒤 text·이전 생성 토큰은 새로 계산하며 오디오 encoder 증분 실행이 아닙니다.

[고정100개 실측](../../results/summaries/optimization/realtime-prefix-cache-20261112.md)에서 CER11.12%·최종210구간 PCM/전사/토큰 ID 동일·실패/드롭/미출력0을 유지하며 최종 합계9.06%·전체9.16%가 줄었습니다. 첫 표시 p95는4.410→4.128초·파일 끝 반환 p95는3.378→2.819초였습니다. 모델 첫 추론은 cold miss이고3회 워밍업 뒤 입력에서는9토큰을 재사용합니다. 순차1쌍·시작 온도/열 제한·부분 호출 수와 문자 철회 차이는 상세 보고서에 기록했습니다.

## 이전 실험 재현

KSS 등 이전 실험 설정은 [설정 색인](../../configs/README.md)에 보존합니다. 소규모 반복·56ms 진단은 [OPT-0](../research/opt0_pilot.md), 별도 C API/ORT 호출 계측은 [OPT-1](../research/opt1_profiling.md), 최초 GGUF/KSS 비교는 [OPT-2](../research/opt2_gguf.md)를 따릅니다. `opt0-baseline`은 현재 `benchmark-qwen3`와 같은 실행기 이름입니다. 프로파일 계측 실행과 일반 성능 측정은 구분합니다.

## 가변 부분 요청 선별

첫 요청 목표는 `--partial-after-s 0.6`/`0.8`도 선택할 수 있습니다. 기존1/2/3초와 CLI 기본2초·공유 개발1초는 유지합니다. 이는 최초 요청에 모으는 음성 길이이고 실제 표시 시각은 입력 프레임·큐·추론에 따라 달라집니다. 후속의 `--partial-min-new-s`나 비용/횟수 예산을 함께 바꾸지 않고 [첫 요청 선별](../research/qwen_realtime.md#q8-첫-요청-단축--2026-11-16-사전-조건)로 비교합니다.

[같은6개 실측](../../results/summaries/optimization/realtime-first-request-20261116.md)에서 두 후보가 통과했고 첫 표시 p50 우선 규칙은0.6초를 선택했습니다.0.8초는 갱신 공백/수정량의 절충이 다릅니다. 측정 웹의 **Q8 첫 요청0.6초 /0.8초 /1초 · 동일6개**를 비교하며100개 확인 전 공유1초는 보존합니다.

[후속100개 확인](../../results/summaries/optimization/realtime-first-request100-20261116.md)은 첫 표시 개선·최종 전사 동등성을 유지했지만 첫4자 긴 지연 한 조건이 미달해 공유1초를 유지했습니다. **Q8 첫 요청1초 /0.6초 · 동일100개**와 저장된 두 기록을 비교할 수 있고 기록 재생은 재추론이 필요하지 않습니다.

기존 대표 명령에 `--partial-mode adaptive --partial-min-new-s 0.8 --partial-tail-margin-s 0.2`를 추가하면 작업자 여유·새 음성량·직전 호출 비용을 기준으로 부분 요청합니다. 첫 요청은 `--partial-after-s`를 따르고 이후 고정 `--partial-interval-s`는 사용하지 않습니다. `--partial-followup-s`와 함께 지정할 수 없습니다. 현재 개발 기본은 고정 간격입니다. [가변 선별 조건](../research/qwen_realtime.md#가변-부분-요청-선별--2026-11-12-사전-조건)을 따르며 최종 구간·PCM은 유지합니다.

가변 판단은 `partial_decision` 이벤트와 `worker.json`의 `partial_scheduler`에 기록합니다. 입력이 도착하는 주기에서 다시 판단하며, 이미 진행 중인 모델 호출은 완료 후 최종 큐를 우선 처리합니다. 끝 여유는 ASR 구간 상한 기준 휴리스틱으로 출력 주기·VAD 종료 지연을 보장하지 않습니다.

가변의 첫 요청 보호·후속 비용 제한 실험은 위 가변 명령에 `--adaptive-policy budgeted --partial-budget-s 2.5 --partial-max-calls 2`를 추가합니다. 최초 요청은 비용 가드로 생략하지 않고, 후속만 종류별 최근 실측 비용·음성 길이·횟수 예산으로 제한합니다. 예산은 후속 요청의 허용 기준이며 실행 중 호출을 강제로 중단하는 제한은 아닙니다. 기본 `--adaptive-policy recent`는 이전 실험을 유지합니다. [보완 선별 조건](../research/qwen_realtime.md#첫-요청-보호비용-예산-가변-선별--2026-11-12-사전-조건).

후속 중복 예약 제거 이후 `budgeted`는 부분 비용 예산과 구간 끝 여유를 별도로 확인합니다. 최종 예상 비용은 진단용으로 남기며 부분 예산에서 재차 차감하지 않습니다. 메타데이터 버전은 `budgeted-headroom-v2`, 이전 중복 예약 정책은 소스 `388b223`입니다. 추가 선별은 `--partial-budget-s 3 --partial-max-calls 2`이며 [사전 조건](../research/qwen_realtime.md#후속-비용-중복-예약-제거--2026-11-12-추가-선별-사전-조건)을 따릅니다.

선택 `--partial-cost-model audio`는 위 `budgeted` 구성에서 음성 처리 단계만 입력 길이에 따라 확대합니다. 단계 계측이 없는 호출은 기본 `total` 추정으로 돌아갑니다. 첫 요청 보호와3초/2회 예산은 유지하며, 고정 기본 설정에는 적용하지 않습니다. [2026-11-13 선별 조건](../research/qwen_realtime.md#음성-단계-비용-추정-선별--2026-11-13-사전-조건).

선택 `--partial-cost-model audio_scaled`는 입력이 짧아질 때도 측정한 음성 처리 비용만 길이에 비례 조정합니다. 생성 등 나머지 비용·최근 3회 최대·20% 여유·단계 계측 누락 시 `total` 복귀는 유지합니다. 선형 근사의 과소 추정 가능성을 별도로 기록하며 기존 `audio`·공유 기본을 바꾸지 않습니다. [문맥 24개 선별 조건](../research/qwen_realtime.md#음성-비용-축소의-연속-입력-선별--2026-11-16-사전-조건).

### 짧은 첫 결과의 후속 보호

가변 `adaptive/budgeted` 명령에 선택 `--protect-short-followup`을 추가하면 첫 성공 부분 결과가 문자/숫자4자 미만인 구간의 후속1회에 비용 예산 예외를 둡니다. 기본은 끔이며 유휴·최소 새 음성·최대 횟수·끝 여유는 유지합니다. 예산3초의 일반 확대와 다르고 확정용5초 입력을 바꾸지 않습니다. 실제 예외는 `partial_decision.short_followup_budget_override`, 활성 정책은 `short-first-budget-v1`로 기록합니다. [사전 조건](../research/qwen_realtime.md#짧은-최초-결과의-후속-1회-보호--2026-11-16-사전-조건).

[100개 끔/켬 실측](../../results/summaries/optimization/realtime-short-followup-20261116.md)에서 최종 정확도는 유지했지만 표시 개선 조건에 미달했습니다. 기본 끔을 유지하며 재현·청취 비교용 선택 옵션으로 보존합니다.

## 빠른 Q4 연속 수정 후보

별도 `configs/listener_continuous.toml`은 Q4_0 본체/출력층·audio Q8·KleidiAI OFF·생성 2T/음성 3T·기존 고정 prefix KV를 사용합니다. 기존 Q8 공유 설정과 Q4 최대 2/3회 프로필은 보존합니다. 다음은 준비된 RP5에서 단일 추론 잠금 안에 새 실행 ID로 실행하는 명령입니다.

```bash
flock -n results/listener/inference.lock env OPENBLAS_NUM_THREADS=1 PYTHONPATH=src \
  .venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output results/optimization/opt3/continuous-example-01 --timeout-s 1800 \
  --temperature-policy operational --skip-temperature-admission -- \
  .venv/bin/python scripts/edge_asr.py tools replay-qwen3 \
  --config configs/quantization/q4_0_head_realtime.toml \
  --manifest data/evaluation/edge100-20261109-01/manifest.jsonl \
  --output results/optimization/opt3/continuous-example-01 \
  --gap-s 0 --vad-threshold 0.3 --vad-preprocess rms26-floor80 \
  --vad-max-speech-s 5 --max-segment-s 5 --partial-after-s 0.6 \
  --partial-mode continuous --partial-min-new-s 0.8 --partial-tail-margin-s 0.2 \
  --token-stream
```

0.8초는 새 입력 최소량이며 표시 주기를 보증하지 않습니다. 새로운 입력과 작업 완료 시 다시 판단하며 최종 우선·열 backoff는 갱신을 늦출 수 있습니다. 횟수/누적 비용 예산과 `adaptive-policy budgeted`는 이 모드에 사용하지 않습니다. 빈 partial은 `failure_kind=empty_partial`, 실패 수 및 실행 실패 상태를 유지합니다. 새 입력으로 회복하며 같은 범위를 즉시 재시도하지 않습니다. `jobs.jsonl`에 후보 합침/폐기·결정·revision·열 변화와 실제 클럭 표본을 기록합니다.

`content_updates`는 같은 문자열 재발행을 제외한 내용 변경 간격과 입력 범위 끝→발행 나이를 제공합니다. 기존 원음별 문자 증가 공백과 VAD 소유 구간의 변경 공백을 구분하며, 둘 다 서버 발행 기준입니다. 실제 브라우저 표시/음향 단어 지연은 아닙니다. 캐시·커널 변경을 섞지 않은 스케줄러 비교를 먼저 수행합니다.

선택 `--cancel-partial-for-final`은 최종 요청이 대기할 때만 실행 중인 partial을 다음 토큰 callback에서 중단합니다. 기본은 끔이며 `continuous`와 `--token-stream`이 필요합니다. 최종 요청 도착 전에는 부분 추론을 끝까지 수행합니다. 취소 원시의 `callback_error`·토큰/바이트와 `cancelled_calls`를 보존하고, 기존 모델 실패·빈 partial 수에서 구분합니다. 어댑터는 취소 후 prefix KV를 초기화하며 다음 final이 재계산합니다. 이미 실행 중인 encoder/prefill은 토큰 callback까지 기다리므로 즉시 선점을 보증하지 않습니다. 기존 bridge는 중도 취소의 decode 단계 시간을 반환하지 않아 취소 호출은 전체 wall 시간만 완전하게 집계합니다.

청취용 선택 프로필은 `configs/listener_continuous_priority.toml`입니다. 최초 연속 모드 실측에서 실행 중 partial에 의한 final 대기가 관측되어 추가한 별도 옵션이며, 취소 없는 `listener_continuous.toml`은 비교용으로 유지합니다.

### 고정 냉각 조건의 지속 부하 조절

새 선택 프로필 `configs/listener_continuous_paced.toml`은 위 명령에 `--continuous-policy paced --partial-thermal-policy pace --cancel-partial-for-final`을 더한 조건입니다. 기본 `latest`·`pause`와 과거 프로필은 그대로 재현할 수 있습니다.

- `pace` 열 정책: 82→85°C에서 후속 새 음성 최소량을 1→1.4배로 늘립니다. 현재 주파수 제한·감속 플래그(2/4/8)이 있으면 최소1.2배를 적용하고 첫0.6초 요청은 유지합니다. 열 감속만으로78°C까지 기다리지 않습니다. 저전압·계측 결측은 부분 요청을 보류하고, 예외 고온90°C 보류/85°C 재개를 별도로 둡니다. 이 값은 앱의 부하 제어 기준이며 기기의 허용 온도 정의가 아닙니다. 펌웨어·OS의 열 보호와 전압/실행 제한은 변경하지 않습니다.
- `paced` 연속 정책: 부분·확정·실패·취소의 실제 추론 wall 시간을 모두 반영합니다. 시간당 계산 목표는 평상시0.7초/초, 열 감속 최대 구간에서는0.5초/초입니다. 초과분이 해소될 때 최신 후보 하나를 실행하며, 새 구간도 초과분을 초기화하지 않습니다. 첫 요청에는0.35초의 제한된 여유를 줍니다. 확정은 이 계산 목표로 보류하지 않으므로 전체 CPU 사용률의 상한을 보증하지 않습니다.
- 확정 보호: 기존 작업자의 최근 음성 비용 추정으로 구간 끝 예약을 조절하되 최대0.8초로 제한합니다. 확정 대기 후0.35초 동안 끝나는 부분 추론은 완료시키고, 그 뒤에도 생성 중이면 다음 토큰 경계에서 취소합니다. 입력·완료·열 표본과 계산 대기 만료가 후보를 깨웁니다.

한 원음의 확정 자막을 확인한 뒤 ‘다음 음원’으로 넘어가는 기록 재생과 수정 가능한 자막 표시는 유지합니다. 위 수치는 비교용 제어값이며 개선율이나 일정한 표시 간격을 뜻하지 않습니다. 같은 입력의 기존 Q4 3회→열 조절만→전체 조절→기존 Q4 3회 선별 후 장시간 부하에서 판정합니다.

[실제20분 대조](../../results/summaries/optimization/continuous-paced-20261117.md)에서는 최종299구간 전사/token·CER21.4464%와 드롭0을 유지하고 추론 합계를9.51% 줄였습니다. 다만 Q4 3회 대비 첫 표시 p95 3.46→4.22초·공백 p95 3.76→4.20초·최종 반환 p95 1.45→1.88초로 늘어 기본 전환은 보류합니다. 이전 연속 모드보다는 개선된 선택 후보이며 빈 확정9건·UTF-8 확정 오류1건은 양쪽 동일하게 남았습니다.

### 정규화 전 특징 캐시 실험

`audio_feature_cache_manifest`와 `audio_feature_cache_mode=off/verify/on`은 별도 preload 실험용입니다. 기본 프로필에는 지정하지 않습니다. 기존 `mtmd_audio_log_mel` 계산을 호출하며, 같은 설정·필터·창 함수·반사 패딩 포함 PCM 창과 일치하는 접두 프레임만 재사용합니다. 나머지 프레임과 입력 전체 최대값 정규화는 다시 계산합니다. Transformer 출력·음성 LLM KV·정규화 후 합성곱 출력은 캐시하지 않습니다.

`verify`는 접두 재사용 결과와 원래 전체 계산을 모두 수행해 float 비트 동등성을 기록합니다. 불일치 때는 원래 결과를 반환하고 캐시를 비웁니다. 검증 실행의 시간은 캐시 성능으로 쓰지 않습니다. `on`은 동등성을 확인한 조건의 비용 비교용이며 공유 기본을 바꾸지 않습니다. 모델의 bridge가 사용하는 mtmd SHA와 preload/manifest를 검증합니다.

최대 501 Mel 프레임(5초 PCM), 지속 캐시 1MiB 이하·임시 공간 1MiB 이하입니다. 입력 prefix/설정 변화·새 segment·final 종료·오류·취소·모델 close에서 무효화합니다. worker는 실행 직전/종료에 상태를 바꾸므로 실행 중 캐시를 외부에서 지우지 않습니다. 같은 입력의 강제 재요청은 스케줄러가 허용하지 않으며 캐시 적중으로 새 갱신을 만들어내지 않습니다.

빌드는 `native/gguf/mel_cache_build.py --source <고정 엔진 소스> --mtmd <현재 libmtmd.so> --output <새 빌드 폴더>`를 사용합니다. 실행 프로세스에 그 `libedge_mel_cache.so`만 `LD_PRELOAD`, 기존 mtmd 경로를 `EDGE_ASR_MEL_MTMD`로 지정합니다. 계측은 `adapter_result.audio_feature_cache`에 원래/재계산 프레임 수·동등성·계산 시간·메모리를 남깁니다. 실제 채택 여부와 조건은 이번 실측 보고서를 따릅니다.

원음별 `observed_first_s`/`observed_first4_s`/`max_content_gap_s`는 이후 실패·취소된 호출이라도 이미 발행한 임시 자막을 포함합니다. 같은 문자열의 재발행을 제외하고 같은 길이 수정·삭제를 내용 변경으로 셉니다. 기존 성공 완료 호출 기준 `first_s`/`first4_s`와 문자 증가 기준 `max_growth_gap_s`는 그대로 보존합니다. 모두 유일하게 귀속되는 원음·서버 발행 기준이며 실제 브라우저 표시나 인식 정확도를 뜻하지 않습니다.

[이번 실제 대조](../../results/summaries/optimization/continuous-q4-20261117.md)에서 새 연속 정책은 Q4 최종 정확도를 유지했지만 장비의 열 보류로100개 표시/확정 지연이 늘었습니다. 기본 채택은 보류하며 청취/개발 후보로 사용합니다. 빠른 Q4 자체는100개에서 빈 final11건이 있었고, 이를 성공 처리하지 않습니다. 선택 Mel 캐시는 비트/전사 동등성을 확인했지만 전체 지연 이득이 불분명해 기본 프로필에서 끕니다.

## 부분 음성 창 캐시 실험

`configs/quantization/q4_0_partial_window.toml`은 기존 Q4 text/output·Q8 audio를 유지하고 새 bridge에서 **부분 요청만** 독립1.99초 창으로 인코딩합니다. `partial_audio_window_s`는1/2/4/8초, 별도 `partial_audio_window_ms`는990/1990ms이며 둘을 함께 지정하지 않습니다. `partial_audio_window_cache=false`는 같은 창 분할의 재계산 대조입니다. 실제 기본 채택 여부와 측정 결과는 현재 상태를 따릅니다.

완료된 창의 embedding과 그에 해당하는 LLM KV를 재사용하고, 꼬리 창은 다시 계산합니다. PCM 바이트가 달라지거나 구간이 바뀌면 캐시를 초기화합니다. 오류·취소·확정 뒤에도 초기화하며, 확정/일반 batch 호출은 기존 전체 입력 경로를 사용합니다. 부분 입력은5초까지로 제한합니다.

**전체 입력 인코딩과 수치 동등한 캐시가 아닙니다.** 창별 Mel 정규화·경계 반사 padding·attention 문맥이 바뀌며, 완료 창은1초당13행만 남겨 끝의 추가 padding sub-chunk를 제외합니다. 최종 CER 외에 부분 전사의 누락/수정도 비교해야 합니다. 기존 log-Mel preload 캐시와 함께 켤 수 없습니다.

설계 참고는 [antirez/qwen-asr](https://github.com/antirez/qwen-asr/tree/924694251d9e0f18e5d86bbd06aa3ab5f870002d)의 완료 인코더 창·변하지 않은 prefill 접두부 재사용입니다. 이 프로젝트에서는 외부 BF16 엔진으로 교체하지 않고 고정 llama.cpp의 기존 Q4 bridge를 확장합니다. 공식 vLLM의 누적 입력 streaming을 RP5에 설치하는 작업도 아닙니다.

정확한1/2초 경계의 STFT는 추가1프레임 때문에 각각2/3개의 합성곱 sub-chunk를 계산합니다.990/1990ms는99/199프레임에 경계1프레임을 더해100/200프레임에 맞춥니다. 원음 샘플은 모두 인접 창/꼬리에 포함하며 삭제하지 않습니다. 새 ms 옵션은 `partial_audio_window_ms_v1` 빌드만 허용하고 기존 초 단위 실험 빌드도 보존합니다.

청취 프로필은 **실험 Q4 · 1.99초 음성 창 캐시**(`configs/listener_partial_window.toml`)입니다. `continuous/latest`·첫0.6초·새 음성0.8초·끝 여유0.2초·확정 우선 즉시 취소·점진적 열 조절을 사용합니다. `paced` 계산량 배분/취소 유예와는 다른 조건이며 화면의 **부분 음성 창** 항목에서 크기·캐시 사용을 확인할 수 있습니다. 기록 재생의 원음별 확정 확인·정지·다음 음원 흐름은 같습니다.
