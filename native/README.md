# C++ 연결·빌드 도구

Python 실행 코드는 [src/edge_asr/qwen/](../src/edge_asr/qwen/), C++ 소스와 빌드는 이 폴더에 둡니다. 외부 llama.cpp·ORT 헤더와 빌드 결과는 로컬 `runtimes/`에 보관하며 기존 Python 환경에 설치하지 않습니다.

| 위치 | 역할 |
| --- | --- |
| [gguf/bridge.cpp](gguf/bridge.cpp) | llama.cpp/libmtmd batch 입력 C ABI. 모델 상주·KV 초기화·오디오/prefill·토큰 decode·선택적 토큰 callback |
| [gguf/build.py](gguf/build.py) | 고정 CPU 엔진에 bridge 연결, `build.json`에 소스/바이너리 해시 기록 |
| [gguf/quantize.py](gguf/quantize.py) | 고정 BF16→Q6/Q5/Q4 독립 변환·자산 명세. 별도 변환 환경·빌드 사용 |
| [ort_trace/ort_trace.cc](ort_trace/ort_trace.cc), [빌드 도구](ort_trace/build.py) | 기존 ORT1.28.2/API28의 Session.Run 호출 계측 |

## GGUF bridge

[OPT-2 계약](../docs/research/opt2_gguf.md)의 고정 llama.cpp commit·text/mmproj Q8_0을 사용합니다. 다음은 RP5 빌드 형식이며 output은 **새 경로**로 지정합니다. 기존 런타임·모델은 덮어쓰지 않습니다.

```bash
.venv/bin/python native/gguf/build.py \
  --source runtimes/qwen3-opt/llama-q8-20261109-01/src \
  --engine-build runtimes/qwen3-opt/llama-q8-20261109-01/build \
  --output "runtimes/qwen3-opt/<새빌드ID>/bridge"
```

현재 검증 구성은 CPU native·GPU OFF·KleidiAI OFF입니다. 모델은1회 로딩하며 기본은 호출별 KV 초기화입니다. 현재 공유 개발 설정은 검증한 고정 text prefix KV만 유지하고 음성 KV의 청크 간 재사용은 미구현입니다. [Python 어댑터](../src/edge_asr/qwen/gguf.py)는 가중치와 연결 라이브러리의 해시를 확인합니다.

새 bridge는 기존 ABI1 batch 호출을 유지하고 `edge_gguf_transcribe_tokens`를 추가합니다. `build.json`의 `features`에 `token_callback_v1`이 기록된 별도 빌드에서만 `replay-qwen3 --token-stream`을 사용할 수 있습니다. callback의 토큰 바이트는 완결된 UTF-8 접두부가 될 때까지 Python에서 보류하며 최종 EOG 결과와 일치하는지 확인합니다. 기존 라이브러리·`configs/gguf.toml`은 보존하고 새 실험 TOML에서 새 library/manifest 경로를 지정합니다. [실측 전 고정 조건](../docs/research/qwen_realtime.md).

`runtimes/qwen3-opt/token-20261111-01/bridge/`는 같은 기존 엔진6개에 연결한 RP5 별도 빌드입니다. [100개 끔/켬 대조](../results/summaries/optimization/realtime-token-20261111.md)에서 최종 PCM·전사·토큰 ID와 CER를 유지하며 첫 표시가 소폭 빨라졌습니다. 기존 기본 설정은 유지하고 [실행 안내](../docs/guides/qwen3_optimization.md)의 새 TOML·선택 옵션으로 사용합니다.

선택 기능 `split_threads_v1`은 기존 `edge_gguf_create`를 보존하고 `edge_gguf_create_threads`에 생성용/음성·prefill용 스레드를 따로 전달합니다. 새 TOML에 `num_threads_batch`를 명시할 때만6인자 함수를 사용합니다. 생략하면 기존5인자 ABI를 사용하며 기존 라이브러리도 유지합니다. 분리 비교 빌드는 `runtimes/qwen3-opt/split-20261111-01/bridge/`처럼 새 경로에 만듭니다. [고정 검증 조건](../docs/research/qwen_realtime.md).

선택 기능 `text_prefix_cache_v1`은 기존 생성 ABI를 유지하고 `edge_gguf_set_prefix_cache(handle, 0/1)`를 추가합니다. 새 GGUF TOML에 `cache_text_prefix = false/true`를 명시할 때 해당 기능·심볼이 필요합니다. 생략하면 기본 끔이며 기존 빌드도 사용합니다. `prefix_cache.h`는 일치한 음성 앞 text만 유지하는 상태 정책을 맡고, 실제 KV 범위 제거는 `bridge.cpp`가 수행합니다. 불일치·제거 실패·호출 실패 시 전체 초기화로 복귀합니다. 음성 embedding·그 뒤 text·이전 생성 결과는 유지하지 않습니다.

새 빌드는 `runtimes/qwen3-opt/prefix-20261112-01/bridge/`처럼 별도 경로를 사용합니다. prefix/suffix text·audio encoder·audio embedding 평가 시간을 native JSON에 분리하며, 기존 `audio_and_prefill_s`도 유지합니다. 캐시 상태·토큰 수·적중·유지 토큰·실패 이유와 첫 추론/워밍업 결과를 기록합니다. 프롬프트 평가를 같은 upstream chunk 순서·함수로 분리했으므로 새 빌드의 캐시 끔 대조도 실제 전사 동등성을 확인합니다. [실측 전 조건](../docs/research/qwen_realtime.md).

[100개 끔/켬 실측](../results/summaries/optimization/realtime-prefix-cache-20261112.md)에서9토큰 유지·전사 동등성·실패 후 복구를 확인했고 사전21조건을 통과했습니다. 공유 개발 설정은 해당 빌드와 `cache_text_prefix=true`를 사용합니다. native 기본값과 기존 빌드/설정은 보존하며 실사용·장시간 채택과 구분합니다.

양자화 준비는 별도 환경에서 upstream `gguf-py`의 numpy·pyyaml·tqdm·requests 의존성을 준비하고, 고정 llama.cpp 소스의 `llama-quantize`를 새 빌드 경로에 만듭니다. 기존 추론 환경·엔진은 변경하지 않습니다. `quantize.py --help`의 source/tool/llama-source/audio-model/output을 지정하며 output은 새 폴더여야 합니다. 모델별 명세는 원본·도구·GGUF type·tensor dtype·SHA를 보존하고 Q8 오디오 파일은 기존 경로를 참조합니다. [실험 조건](../docs/research/qwen_realtime.md).

오디오 Q4 실험은 같은 고정 `llama-quantize`의 `--tensor-type-file`을 사용합니다. 공식 BF16 mmproj에서 기존 Q8 배포본이 양자화한110개 행렬만 Q4_0으로 바꾸고 나머지192개 tensor의 dtype/바이트를 보존했습니다. 주요 폭896은 K 방식의256블록과 맞지 않아 audio는 Q4_0을 사용하며 text Q4_K_M과 구분합니다. 새 자산은 `models/qwen3-opt/gguf-audio-quant-20261113-01/`이며 원본·명령·tensor별 정밀도·해시는 해당 명세에 기록합니다. 기존 Q8을 입력으로 재양자화하지 않습니다. [text/audio 모두 Q4 비교 조건](../docs/research/qwen_realtime.md#textaudio-모두-q4-대조--2026-11-13-사전-조건).

## ORT 계측

[OPT-1 프로토콜](../docs/research/opt1_profiling.md)을 따릅니다. 공식 v1.28.2의 [C API](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_c_api.h), [error code](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_error_code.h), [EP API](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_ep_c_api.h) 헤더를 별도 `runtimes/.../include/`에 준비합니다. MIT 라이선스·원본 저작권 표시를 유지하며 빌드 도구가 고정 헤더 SHA-256을 검사합니다.

```bash
.venv/bin/python native/ort_trace/build.py \
  --headers runtimes/qwen3-opt/ort-trace-20261109-01/include \
  --output "runtimes/qwen3-opt/<새빌드ID>/trace"
```

실제 **자식** 실행에만 trace 라이브러리 절대 경로를 `LD_PRELOAD`로, 기존 환경의 `libonnxruntime.so` 절대 경로를 `EDGE_ASR_ORT_LIBRARY`로 지정합니다. guard에는 preload하지 않습니다. 동일 프로세스에서 순차 계측하며 `tools profile-qwen3 --help`의 on/off/ABBA 옵션을 사용합니다. 내부 logits 시각은 사용자 TTFT와 구분합니다.

Host/RP5 모의·문법 검사는 [tests/qwen/](../tests/qwen/)에 있습니다. `EDGE_ASR_LLAMA_SOURCE`와 `EDGE_ASR_ORT_HEADERS`를 지정하면 고정 헤더의 C++ 검사도 실행합니다. 실제 모델 성능은 RP5 실측으로만 확인합니다.

## GGUF 연산별 진단

`build.py --profile-operators`로 새 경로에 빌드하면 `operator_profile_v1`과 `edge_gguf_set_operator_profile(handle, 0/1)`을 추가합니다. Python 어댑터의 `set_operator_profile(bool)`로 호출 사이에 켜고 끕니다. 기본 빌드와 공유 실행 설정은 그대로입니다. [operator_profile.h](gguf/operator_profile.h)는 단계·연산·tensor 이름·입력 dtype별 시간과 횟수를8192그룹까지 모으며, 콜백 불일치·역전 시계·용량 초과를 실패로 드러냅니다. 같은 그룹의 shape 변화도 표시하고 tensor payload는 읽지 않습니다.

같은 고정 llama.cpp의 LLM/mtmd 공개 `cb_eval`을 사용합니다. 계측 켬은 각 node를 별도 graph view로 계산하고 동기화하므로 fusion·스케줄·실행 시간이 달라질 수 있습니다. **계측 시간은 운영 성능값으로 쓰지 않고**, 같은 PCM의 기존 빌드·새 빌드 끔·새 빌드 켬·새 빌드 끔에서 전사/token ID와 비용을 비교합니다. 단계 합계와 node 합계의 잔여 시간도 구분합니다. [이번 프로파일링 조건](../docs/research/qwen_realtime.md#q4-연산별-프로파일링--2026-11-13-사전-조건).

## Q4 prefill의 repack 대조

고정된 현재 CPU 경로에서 `GGML_CPU_REPACK`을 별도 엔진 빌드마다 비교합니다. 기존 CMake 옵션을 기록하고 새 빌드 경로에서 해당 항목만 바꿉니다. 기존 `build.py`는 같은 고정 소스·native CPU/GPU OFF/KleidiAI OFF 계약으로 새 엔진에 bridge를 연결합니다. 런타임/설정만 바꾸고 가중치는 유지하며, [고정 PCM→연속 입력 선별 조건](../docs/research/qwen_realtime.md#q4-prefill-실행-경로-선별--2026-11-13-사전-조건)을 따릅니다. 켬/끔의 성능·수치 동등성을 가정하지 않습니다.

[이번 Q4 선별](../results/summaries/optimization/prefill-q4-20261113.md)에서는 repack OFF가 느려져 기존 ON을 유지했습니다. 선택된 음성/prefill4T는 기존 prefix bridge의 스레드 설정만 바꿉니다. 새 OFF 엔진은 부정적 결과의 자산으로 보존합니다.

## GGUF CPU 커널 호출 추적

[gguf/kernel_trace.cpp](gguf/kernel_trace.cpp)는 고정 upstream의 공개 ELF 심볼을 `LD_PRELOAD`로 감싸 원래 함수를 그대로 호출하는 선택 진단입니다. 기존 bridge·엔진을 재빌드하거나 패키지를 설치하지 않습니다. Q4_K/Q6_K/Q8_0의 repack GEMM/GEMV 선택·호출 수·MAC 수, F32 입력의 Q8 활성값 변환을 단계별로 집계합니다. 오디오 Q4_0 행렬곱·일반 MUL_MAT·barrier·커널 내부 bit unpack/scale/dot은 분리 계측하지 않습니다.

빌드는 같은 고정 llama.cpp 헤더의 `include`·`ggml/include`·`tools/mtmd`를 `-I`로 전달하고 `g++ -std=c++17 -O2 -shared -fPIC -Wall -Wextra -Werror native/gguf/kernel_trace.cpp -ldl -pthread -o <새 경로>/libedge_kernel_trace.so`를 실행합니다. 실제 추론 자식에만 `LD_PRELOAD`를 지정하고 `EDGE_ASR_TRACE_CPU`·`EDGE_ASR_TRACE_MTMD`·`EDGE_ASR_TRACE_LLAMA`에는 기존에 로딩되는 각 `.so`의 절대 경로를 넣습니다. 누락된 라이브러리/심볼은 실패로 드러내며 다른 계산 경로로 대체하지 않습니다.

직렬 호출 사이에서 `edge_kernel_trace_begin(0/1)`으로 집계를 초기화하고 모드를 고릅니다. `edge_kernel_trace_result()`는 JSON을 반환하며 실행 중 초기화/조회는 거부합니다. 집계 공간은 고정 크기이고 중첩된 입력 변환을 중복 합산하지 않습니다. 단계 구분은 기존 mtmd helper와 `llama_process` 호출 범위입니다. **작업자별 wall/CPU 합계는 병렬 구간의 사용자 대기시간이 아닙니다.** 끔/켬의 전사·token ID·MAC 동등성과 계측 비용을 대조하며 운영 기본에는 적용하지 않습니다. [사전 조건](../docs/research/qwen_realtime.md#q4-3t-커널-추적--2026-11-13-사전-조건).
