# C++ 연결·빌드 도구

Python 실행 코드는 [src/edge_asr/qwen/](../src/edge_asr/qwen/), C++ 소스와 빌드는 이 폴더에 둡니다. 외부 llama.cpp·ORT 헤더와 빌드 결과는 로컬 `runtimes/`에 보관하며 기존 Python 환경에 설치하지 않습니다.

| 위치 | 역할 |
| --- | --- |
| [gguf/bridge.cpp](gguf/bridge.cpp) | llama.cpp/libmtmd batch 입력 C ABI. 모델 상주·KV 초기화·오디오/prefill·토큰 decode·선택적 토큰 callback |
| [gguf/build.py](gguf/build.py) | 고정 CPU 엔진에 bridge 연결, `build.json`에 소스/바이너리 해시 기록 |
| [gguf/kernel_trace.cpp](gguf/kernel_trace.cpp) | 기존 엔진의 선택 ELF 함수 호출 추적. 운영 기본과 별도 |
| [gguf/conv_sgemm.cpp](gguf/conv_sgemm.cpp), [패치](gguf/conv_sgemm.patch) | 고정 합성곱2/3의 원래/재컴파일6열/4열 비교. 원래 GGML 누적·threadpool 재사용 |
| [gguf/f16_dot.cpp](gguf/f16_dot.cpp) | FP16×FP32 변환·내적 결합 선택 실험. 64의 배수만 대체하며 나머지는 원래 함수로 전달 |
| [gguf/q8_gemv.cpp](gguf/q8_gemv.cpp) | Q8 GEMV 누적 분리·prefetch 선택 실험. 원래 함수/계산 대조 |
| [gguf/quantize.py](gguf/quantize.py) | 고정 BF16→Q6/Q5/Q4_K_M/Q4_0·출력층 독립 변환·자산 명세. 별도 변환 환경·빌드 사용 |
| [ort_trace/ort_trace.cc](ort_trace/ort_trace.cc), [빌드 도구](ort_trace/build.py) | 기존 ORT1.28.2/API28의 Session.Run 호출 계측 |

## GGUF bridge

[OPT-2 계약](../docs/research/opt2_gguf.md)의 고정 llama.cpp commit·text/mmproj Q8_0을 사용합니다. 다음은 RP5 빌드 형식이며 output은 **새 경로**로 지정합니다. 기존 런타임·모델은 덮어쓰지 않습니다.

```bash
.venv/bin/python native/gguf/build.py \
  --source runtimes/qwen3-opt/llama-q8-20261109-01/src \
  --engine-build runtimes/qwen3-opt/llama-q8-20261109-01/build \
  --output "runtimes/qwen3-opt/<새빌드ID>/bridge"
```

공유 기본은 CPU native·GPU OFF·KleidiAI OFF입니다. 별도 KleidiAI ON 엔진에는 `build.py --kleidiai`를 명시해야 하며 CMake cache와 요청의 일치를 확인합니다. `build.json`은 KleidiAI/repack/OpenMP 실제 옵션을 기록합니다. 모델은1회 로딩하며 기본은 호출별 KV 초기화입니다. 현재 공유 개발 설정은 검증한 고정 text prefix KV만 유지하고 음성 KV의 청크 간 재사용은 미구현입니다. [Python 어댑터](../src/edge_asr/qwen/gguf.py)는 가중치와 연결 라이브러리의 해시를 확인합니다.

새 bridge는 기존 ABI1 batch 호출을 유지하고 `edge_gguf_transcribe_tokens`를 추가합니다. `build.json`의 `features`에 `token_callback_v1`이 기록된 별도 빌드에서만 `replay-qwen3 --token-stream`을 사용할 수 있습니다. callback의 토큰 바이트는 완결된 UTF-8 접두부가 될 때까지 Python에서 보류하며 최종 EOG 결과와 일치하는지 확인합니다. 기존 라이브러리·`configs/gguf.toml`은 보존하고 새 실험 TOML에서 새 library/manifest 경로를 지정합니다. [실측 전 고정 조건](../docs/research/qwen_realtime.md).

`runtimes/qwen3-opt/token-20261111-01/bridge/`는 같은 기존 엔진6개에 연결한 RP5 별도 빌드입니다. [100개 끔/켬 대조](../results/summaries/optimization/realtime-token-20261111.md)에서 최종 PCM·전사·토큰 ID와 CER를 유지하며 첫 표시가 소폭 빨라졌습니다. 기존 기본 설정은 유지하고 [실행 안내](../docs/guides/qwen3_optimization.md)의 새 TOML·선택 옵션으로 사용합니다.

선택 기능 `split_threads_v1`은 기존 `edge_gguf_create`를 보존하고 `edge_gguf_create_threads`에 생성용/음성·prefill용 스레드를 따로 전달합니다. 새 TOML에 `num_threads_batch`를 명시할 때만6인자 함수를 사용합니다. 생략하면 기존5인자 ABI를 사용하며 기존 라이브러리도 유지합니다. 분리 비교 빌드는 `runtimes/qwen3-opt/split-20261111-01/bridge/`처럼 새 경로에 만듭니다. [고정 검증 조건](../docs/research/qwen_realtime.md).

선택 기능 `text_prefix_cache_v1`은 기존 생성 ABI를 유지하고 `edge_gguf_set_prefix_cache(handle, 0/1)`를 추가합니다. 새 GGUF TOML에 `cache_text_prefix = false/true`를 명시할 때 해당 기능·심볼이 필요합니다. 생략하면 기본 끔이며 기존 빌드도 사용합니다. `prefix_cache.h`는 일치한 음성 앞 text만 유지하는 상태 정책을 맡고, 실제 KV 범위 제거는 `bridge.cpp`가 수행합니다. 불일치·제거 실패·호출 실패 시 전체 초기화로 복귀합니다. 음성 embedding·그 뒤 text·이전 생성 결과는 유지하지 않습니다.

새 빌드는 `runtimes/qwen3-opt/prefix-20261112-01/bridge/`처럼 별도 경로를 사용합니다. prefix/suffix text·audio encoder·audio embedding 평가 시간을 native JSON에 분리하며, 기존 `audio_and_prefill_s`도 유지합니다. 캐시 상태·토큰 수·적중·유지 토큰·실패 이유와 첫 추론/워밍업 결과를 기록합니다. 프롬프트 평가를 같은 upstream chunk 순서·함수로 분리했으므로 새 빌드의 캐시 끔 대조도 실제 전사 동등성을 확인합니다. [실측 전 조건](../docs/research/qwen_realtime.md).

[100개 끔/켬 실측](../results/summaries/optimization/realtime-prefix-cache-20261112.md)에서9토큰 유지·전사 동등성·실패 후 복구를 확인했고 사전21조건을 통과했습니다. 공유 개발 설정은 해당 빌드와 `cache_text_prefix=true`를 사용합니다. native 기본값과 기존 빌드/설정은 보존하며 실사용·장시간 채택과 구분합니다.

양자화 준비는 별도 환경에서 upstream `gguf-py`의 numpy·pyyaml·tqdm·requests 의존성을 준비하고, 고정 llama.cpp 소스의 `llama-quantize`를 새 빌드 경로에 만듭니다. 기존 추론 환경·엔진은 변경하지 않습니다. `quantize.py --help`의 source/tool/llama-source/audio-model/output을 지정하며 output은 새 폴더여야 합니다. 기본 변환 목록은 이전 Q6/Q5/Q4_K_M이며 `--quantization q4_0`으로 Q4_0만 만들 수 있습니다. `--output-tensor-type q4_0`은 출력층만 명시적으로 바꾼 별도 이름의 후보를 만듭니다. 변환 후 `output.weight`의 실제 dtype·차원·바이트 수를 검증하고 명세에 기록합니다. 모델별 명세는 원본·도구·GGUF type·tensor dtype·SHA를 보존하고 Q8 오디오 파일은 기존 경로를 참조합니다. [실험 조건](../docs/research/qwen_realtime.md).

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

## Hailo-8L Qwen 합성곱·CNN 출력 투영 실험

[ONNX 추출](gguf/export_audio_boundaries.py)은 고정 Q8 GGUF의 가중치를 검증합니다. [선택 합성곱](gguf/hailo_conv.cpp)은 Linux CPU 엔진의 지정 Conv 한 개를 무편향 HEF로 바꿉니다. 원래 노드가 bias·GELU를 적용합니다. HailoRT 헤더와 라이브러리로 별도 빌드한 뒤 실험 자식에서만 preload합니다. `EDGE_ASR_HAILO_LAYER`는 `a.conv2d.1.weight` 또는 `a.conv2d.2.weight`, `EDGE_ASR_HAILO_HEF`는 그 층의 검증한 무편향 HEF 절대 경로입니다. HEF 및 가중치 해시와 실제 호출의 `edge_hailo_conv` 기록을 대조합니다. 장치 오류는 실험 자식의 실패로 기록합니다.

CNN 출력 투영만 선택하려면 실험 자식에 같은 preload와 `EDGE_ASR_HAILO_PROJECTION_HEF=/절대/경로/conv_out_row.hef`를 지정합니다. 위 Conv2 환경변수를 함께 지정하면 두 HEF가 하나의 Hailo 장치와 round-robin 스케줄러를 공유합니다. 투영 입력은 원래 GGML 그래프가 준비한 시간 행당 7,680개 FP32 값이고 출력은 같은 순서의 896개 FP32 값입니다. HailoRT가 호스트 FP32와 HEF INT8 사이의 변환을 수행합니다. 호출마다 `edge_hailo_projection`의 행 수와 동기 시간, 레이아웃 처리 시간을 남깁니다. 최종 음성 임베딩 투영과 LLM은 CPU가 계속 실행합니다.

세 합성곱을 묶으려면 `EDGE_ASR_HAILO_CNN_STACK_HEF`에 `qwen_cnn_stack.hef`의 절대 경로를 지정합니다. 단일 층을 고르는 `EDGE_ASR_HAILO_LAYER`와는 함께 지정할 수 없습니다. 추출기의 `cnn_stack.onnx`에는 각 층의 bias와 원래 Erf GELU 세 개가 들어 있습니다. 고정 Qwen 그래프에서 `inp_chunks`부터 `after_conv_blocks`까지를 단일 노드로 연결하고 투영 전 재배열은 원래 GGML이 수행합니다. 선택한 실행에서 `edge_hailo_cnn_stack` 콜백이 기록돼야 합니다.

같은 preload에서 CNN 뒤 투영까지 묶거나 첫 오디오 블록과 최종 음성 MLP를 선택할 수 있습니다. 아래 변수의 HEF는 모두 같은 Q8 모델에서 추출해 Hailo-8L용으로 컴파일합니다.

| 환경 변수 | ONNX 또는 HEF | 실행 경계 |
| --- | --- | --- |
| `EDGE_ASR_HAILO_FUSED_HEF` | `cnn_projected.onnx`, `cnn_slice.onnx` 또는 `cnn_pack.onnx` | Mel 청크 → 세 Conv·GELU → CNN 출력 투영. 출력은 시간 행 13×896입니다. |
| `EDGE_ASR_HAILO_CONV23_HEF` | `conv23.onnx` | CPU가 첫 GELU까지 계산한 뒤 Conv2·3 및 GELU를 한 번에 호출합니다. |
| `EDGE_ASR_HAILO_BLOCK_DIR` | 디렉터리 안의 `block026.hef`, `block052.hef`, `block078.hef` | 위치 임베딩 뒤 첫 Transformer 블록 전체입니다. 길이 26·52·78에서 해당 HEF를 고르고 나머지 길이에서는 CPU 블록을 사용합니다. |
| `EDGE_ASR_HAILO_EMBED_HEF` | `projector-row.onnx` | 마지막 오디오 정규화 뒤 896→896→1,024 음성 MLP입니다. |
| `EDGE_ASR_HAILO_TEMPERATURE=1` | 선택 계측 | Hailo 콜백에서 약 10초 간격으로 TS0·TS1 센서를 콘솔에 기록합니다. |

CNN 시작 경로는 단일 Conv, Conv2·3, 세 층 묶음, 투영까지 묶음 중 하나를 고릅니다. 세 층 묶음 또는 Conv2·3과 별도 CNN 투영을 비교할 때는 `EDGE_ASR_HAILO_PROJECTION_HEF`를 함께 지정합니다. 여러 HEF는 같은 VDevice의 스케줄러를 사용합니다. 독립 QKV 검산은 기존 추출기에 `--linear-reference`, `--text-gguf`, `--qkv`를 함께 전달합니다.

## GGUF 연산별 진단

2026-11-20 이후 새로 만든 계측 빌드는 그룹별 최초 관측 순서와 모든 작업 스레드를 합친 프로세스 CPU 초를 함께 기록합니다. 기존 빌드의 기록에는 이 필드가 없습니다. 별도 `--profile-operators --capture-boundaries` 빌드에만 `EDGE_ASR_QWEN_BOUNDARY_DIR`로 단일 창·단일 호출의 빈 출력 폴더를 지정할 수 있습니다. 계측을 켜면 Qwen의 DFT·Mel 계수와 입출력, 합성곱·첫 Transformer 블록·최종 투영, 각 LLM 단계에서 첫 FFN up 행렬곱의 첫 입출력을 GGML 차원 역순의 파일명과 연속 FP32 바이트로 보존합니다. 같은 이름 충돌·형식 오류는 유효하지 않은 trace로 처리합니다. 복사·파일 쓰기가 있는 호출은 지연 대조에서 제외합니다.


`build.py --profile-operators`로 새 경로에 빌드하면 `operator_profile_v1`과 `edge_gguf_set_operator_profile(handle, 0/1)`을 추가합니다. Python 어댑터의 `set_operator_profile(bool)`로 호출 사이에 켜고 끕니다. 기본 빌드와 공유 실행 설정은 그대로입니다. [operator_profile.h](gguf/operator_profile.h)는 단계·연산·tensor 이름·입력 dtype별 시간과 횟수를8192그룹까지 모으며, 콜백 불일치·역전 시계·용량 초과를 실패로 드러냅니다. 같은 그룹의 shape 변화도 표시하고 tensor payload는 읽지 않습니다.

같은 고정 llama.cpp의 LLM/mtmd 공개 `cb_eval`을 사용합니다. 계측 켬은 각 node를 별도 graph view로 계산하고 동기화하므로 fusion·스케줄·실행 시간이 달라질 수 있습니다. **계측 시간은 운영 성능값으로 쓰지 않고**, 같은 PCM의 기존 빌드·새 빌드 끔·새 빌드 켬·새 빌드 끔에서 전사/token ID와 비용을 비교합니다. 단계 합계와 node 합계의 잔여 시간도 구분합니다. [이번 프로파일링 조건](../docs/research/qwen_realtime.md#q4-연산별-프로파일링--2026-11-13-사전-조건).

## Q4 prefill의 repack 대조

고정된 현재 CPU 경로에서 `GGML_CPU_REPACK`을 별도 엔진 빌드마다 비교합니다. 기존 CMake 옵션을 기록하고 새 빌드 경로에서 해당 항목만 바꿉니다. `build.py`는 같은 고정 소스·native CPU/GPU OFF와 명시한 KleidiAI 상태를 검사해 새 엔진에 bridge를 연결합니다. 아래 11월13일 비교는 KleidiAI OFF였습니다. 런타임/설정만 바꾸고 가중치는 유지하며, [고정 PCM→연속 입력 선별 조건](../docs/research/qwen_realtime.md#q4-prefill-실행-경로-선별--2026-11-13-사전-조건)을 따릅니다. 켬/끔의 성능·수치 동등성을 가정하지 않습니다.

[이번 Q4 선별](../results/summaries/optimization/prefill-q4-20261113.md)에서는 repack OFF가 느려져 기존 ON을 유지했습니다. 선택된 음성/prefill4T는 기존 prefix bridge의 스레드 설정만 바꿉니다. 새 OFF 엔진은 부정적 결과의 자산으로 보존합니다.

## GGUF CPU 커널 호출 추적

[gguf/kernel_trace.cpp](gguf/kernel_trace.cpp)는 고정 upstream의 공개 ELF 심볼을 `LD_PRELOAD`로 감싸 원래 함수를 그대로 호출하는 선택 진단입니다. 기존 bridge·엔진을 재빌드하거나 패키지를 설치하지 않습니다. Q4_K/Q6_K/Q8_0의 repack GEMM/GEMV 선택·호출 수·MAC 수, F32 입력의 Q8 활성값 변환을 단계별로 집계합니다. 오디오 Q4_0 행렬곱·일반 MUL_MAT·barrier·커널 내부 bit unpack/scale/dot은 분리 계측하지 않습니다.

빌드는 같은 고정 llama.cpp 헤더의 `include`·`ggml/include`·`tools/mtmd`를 `-I`로 전달하고 `g++ -std=c++17 -O2 -shared -fPIC -Wall -Wextra -Werror native/gguf/kernel_trace.cpp -ldl -pthread -o <새 경로>/libedge_kernel_trace.so`를 실행합니다. 실제 추론 자식에만 `LD_PRELOAD`를 지정하고 `EDGE_ASR_TRACE_CPU`·`EDGE_ASR_TRACE_MTMD`·`EDGE_ASR_TRACE_LLAMA`에는 기존에 로딩되는 각 `.so`의 절대 경로를 넣습니다. 누락된 라이브러리/심볼은 실패로 드러내며 다른 계산 경로로 대체하지 않습니다.

직렬 호출 사이에서 `edge_kernel_trace_begin(0/1)`으로 집계를 초기화하고 모드를 고릅니다. `edge_kernel_trace_result()`는 JSON을 반환하며 실행 중 초기화/조회는 거부합니다. 집계 공간은 고정 크기이고 중첩된 입력 변환을 중복 합산하지 않습니다. 단계 구분은 기존 mtmd helper와 `llama_process` 호출 범위입니다. **작업자별 wall/CPU 합계는 병렬 구간의 사용자 대기시간이 아닙니다.** 끔/켬의 전사·token ID·MAC 동등성과 계측 비용을 대조하며 운영 기본에는 적용하지 않습니다. [사전 조건](../docs/research/qwen_realtime.md#q4-3t-커널-추적--2026-11-13-사전-조건).

[실제96호출](../results/summaries/optimization/kernel-q4-20261113.md)에서 같은 모델의 기존 전사/token ID와 단계별 연산량을 유지했습니다.3T에서도 Q4 prefill의 입력 변환과 GEMM 비용 증가를 확인했으며, 커널 내부 unpack/scale/dot은 후속 계측 범위입니다.

선택 PMU 진단은 [gguf/perf_counters.h](gguf/perf_counters.h)의 Linux `perf_event_open` 그룹을 사용합니다. `edge_kernel_trace_set_pmu(bank, type)`에서 bank 0은 끔, 1은 cycle/instruction/L1D·L2D access/refill, 2는 cycle/instruction/frontend·backend stall/bus/L1D TLB refill입니다. type은 장비 PMU sysfs에서 읽으며 권한·sysctl을 변경하지 않습니다. 현재 작업자 스레드의 사용자 공간만 세고, pinned 그룹의 enabled/running 시간·실패 횟수/errno를 함께 반환합니다. 실패·불완전 계측은 성능 0으로 해석하지 않습니다. 모드 변경 시 각 작업자는 다음 최상위 커널에서 이전 그룹을 닫습니다.

`edge_kernel_trace_set_output_shape(width, stride)`는 별도로 확인한 출력층 모양의 Q8 GEMV만 분리하며 `(0, 0)`은 분류 끔입니다. 작업자별 호출·MAC·CPU 합계도 최대64개 슬롯까지 기록하고 초과를 표시합니다. PMU 읽기는 기존 커널 타이머 밖에 있지만 native 전체 비용에는 들어갑니다. 두 PMU bank는 서로 다른 호출에서 관측하며 cache/bus 횟수를 DRAM 대역폭으로 환산하지 않습니다. 기존 시간 전용·끔 API와 운영 설정은 유지합니다. [PMU 사전 조건](../docs/research/qwen_realtime.md#q8-cpu캐시작업자-진단--2026-11-17-사전-조건).

[RP5 PMU 진단](../results/summaries/optimization/pmu-q8-20261117.md)에서 실제36호출의 전사/token을 유지하고997,038커널의 실패0·coverage100%를 확인했습니다. 이번 native 합계의 계측 비용은끔 대비약1~2.5%이며 운영 엔진에는 연결하지 않습니다.

## Q8 GEMV 누적 분리 후보

[gguf/q8_gemv.cpp](gguf/q8_gemv.cpp)는 고정 Q8 4x4 GEMV의 int32 누적을 둘로 나누는 선택 실험입니다. 기존 엔진·가중치 배치를 보존하며 decode와 prefill 잔여 행에 사용되는 해당 심볼만 대체합니다. 기본 모드는 원래 함수 전달입니다. `EDGE_ASR_Q8_CPU`에 이미 로딩된 기존 CPU 라이브러리 경로를 지정하고, 별도 실험 자식에만 새 라이브러리를 `LD_PRELOAD`로 지정합니다. `edge_q8_gemv_begin(0/1)`은 직렬 모델 호출 사이에서만 사용하며 미지원 ARM 빌드에서 켬을 거부합니다. 전달/대체 횟수는 `edge_q8_gemv_calls(0/1)`로 확인합니다.

빌드는 기존 엔진의 `build.ninja`에 기록된 ARM ISA·`-O3` 조건과 고정 upstream의 `ggml/include`·`ggml/src`·`ggml/src/ggml-cpu` 헤더를 사용하며 output은 새 경로에 둡니다. 원래 라이브러리와 합성 입력의 float 비트 일치부터 검증합니다. 공유 설정에는 연결하지 않으며 [선별 조건](../docs/research/qwen_realtime.md#q8-gemv-정수-누적-분리--2026-11-16-사전-조건)에 따라 실제 전사/token 동등성과 비용을 비교합니다.

[RP5 선별 결과](../results/summaries/optimization/decode-q8-20261116.md)는 합성72조건·실제36호출 동등성을 유지했지만 속도 기준에 미달했습니다. 운영 기본에는 적용하지 않으며 후보 소스·별도 빌드·부정적 결과를 재현용으로 보존합니다.


## Q8 가중치 사전 읽기 후보

같은 [gguf/q8_gemv.cpp](gguf/q8_gemv.cpp)의 계산 템플릿을 재사용합니다. 기존 `edge_q8_gemv_begin(0/1)`은 원래 함수/정수 누적 분리로 유지하고, 새 `edge_q8_gemv_prefetch_begin(0/4/8)`은 원래 정수 누적 순서에 사전 읽기0/4/8 packed 블록을 지정합니다. 한 블록은136바이트로, 거리0은 재컴파일된 무-prefetch 대조입니다. `edge_q8_gemv_mode()`는 실제 모드0/1/2/3/4를 반환합니다. 지원되지 않는 거리·ARM ISA·실행 중 전환은 거부하고 기존 값/집계를 보존합니다.

미래 블록이 호출자의 가중치 범위 안에 있을 때만 주소0/64/128바이트에 읽기·locality3 힌트를 넣습니다. 계산 순서·가중치 배치·GEMM·스레드는 바꾸지 않습니다. 새 빌드/실험 자식에만 연결하며 공유 설정과 과거 바이너리는 보존합니다. [고정 PCM 선별 조건](../docs/research/qwen_realtime.md#q8-가중치-prefetch--2026-11-17-사전-조건).

[RP5 prefetch 선별](../results/summaries/optimization/prefetch-q8-20261117.md)에서 합성84조건의336비교와 실제60호출 전사/token 동등성을 확인했습니다. 두 거리 모두 원래/무-prefetch 대조 대비 속도 기준에 미달해 운영 기본에는 연결하지 않습니다.

## FP16 변환·내적 선택 실험

`gguf/f16_dot.cpp`는 고정 ARM NEON `ggml_vec_dot_f16_f32`의64배수 입력만 대체합니다. `EDGE_ASR_F16_CPU`에 이미 로딩한 원래 CPU 라이브러리 경로를 지정하고 실험 자식에만 `LD_PRELOAD`합니다. `edge_f16_dot_begin(0/1)`으로 전달/결합을 선택하며 기본은0입니다. `edge_f16_dot_calls(replaced,bucket)`의 bucket은9/4320/7680/기타 길이입니다. 제어·집계 조회는 추론이 끝난 유휴 상태에서만 하며 worker64개 초과는 overflow로 검출합니다. 기존 엔진/bridge에 설치하거나 공유 설정에서 자동 활성화하지 않습니다. [사전 검증 조건](../docs/research/qwen_realtime.md#encoder-fp16-변환내적-결합--2026-11-17-사전-조건).

## FP16 합성곱 타일 선택 실험

`conv_sgemm.patch`는 고정 upstream `ggml-cpu/llamafile/sgemm.cpp`의 첫 FP 타일 선택2줄만 바꿉니다. 원본에 적용하지 않고 `patch --fuzz=0 --output <새 사본> <원본> native/gguf/conv_sgemm.patch`로 새 빌드 경로에 생성합니다. 원래 컴파일 옵션으로 `EDGE_CONV_COLUMNS=6/4`·`llamafile_sgemm=edge_conv6/edge_conv4`를 각각 정의해 빌드한 뒤 선택 래퍼와 연결합니다. 원래 누적/축약·동적 작업 분배 코드는 그대로이며 열 타일에 따라 작업 묶음의 타일 수12→18로 조절해 약72열 단위를 유지합니다.

`EDGE_ASR_CONV_CPU`는 이미 로딩한 원래 CPU 경로입니다. 실험 자식에만 preload하고 `edge_conv_begin(0/1/2)`로 원래 함수/재컴파일6열/4열을 선택합니다. FP16×FP16→FP32·3T·m=800c 또는208c(c=1~6)·n480·k4320·연속 행만 대상으로 하며 나머지는 원래 함수로 전달합니다. `edge_conv_result()`는 실제 accepted 호출·작업자별 CPU/wall 시간이며 병렬 wall 합을 단일 추론 지연으로 해석하지 않습니다. 제어/집계 조회는 유휴 상태에서만 합니다. [사전 조건](../docs/research/qwen_realtime.md#fp16-합성곱-타일-선별--2026-11-17-사전-조건).

선택 연속 입력은 모델 설정의 `conv_kernel_manifest`가 가리키는 `conv_tile4_1to6_v1` 빌드 명세를 사용합니다. 명세는 repo 기준 `library`/`cpu_library`, 두 SHA-256, 고정 `upstream_commit`을 포함합니다. 실행 프로세스를 시작할 때 해당 후보 하나의 `LD_PRELOAD`와 원래 CPU의 `EDGE_ASR_CONV_CPU`를 지정해야 합니다. `qwen/convolution.py`가 해시·실제 전역 심볼을 확인하고 직렬 호출마다 mode2를 초기화/기록합니다. 1~6청크 외에는 원래 함수로 전달하며 기본 설정은 변경하지 않습니다.

5초 입력은 전처리의 끝 프레임 패딩으로6청크를 사용합니다. 1~5청크 진단 빌드는 보존하며1~6청크 후보는 새 실행 ID/빌드 명세로 구분합니다.

선택 어댑터를 `close()`하면 mode0으로 복원합니다. 같은 프로세스에서 후속 기본 어댑터를 열어도 후보 선택이 남지 않도록 하며 모드 변경과 모델 종료는 추론 호출이 끝난 유휴 상태에서만 수행합니다.

## 정규화 전 log-Mel 접두 캐시 실험

`gguf/mel_prefix_cache.h`는 반사 패딩을 포함한 같은 입력 창의 원시 log-Mel 프레임을 재사용합니다. `mel_prefix_cache.cpp`는 별도 preload로 원래 mtmd 계산을 호출하고, `mel_cache_build.py`가 고정 소스·기존 mtmd SHA를 기록한 새 라이브러리를 만듭니다. 기존 엔진·모델은 변경하지 않습니다. off/verify/on을 분리하며 verify의 불일치는 원래 전체 결과로 복귀합니다. 수명·메모리·실행 옵션은 [Qwen 실행 안내](../docs/guides/qwen3_optimization.md#정규화-전-특징-캐시-실험)를 확인합니다.

## 부분 음성 창 선택 실험

`gguf/audio_window_cache.h`와 기존 `gguf/bridge.cpp`의 `partial_audio_window_v1`/`partial_audio_window_ms_v1`을 별도 빌드에서 사용합니다. 기존 엔진·가중치는 그대로 두고 새 bridge 경로에 빌드합니다. 창 캐시/음성 KV는 부분 요청에만 적용하며 확정은 원래 전체 구간을 계산합니다. 캐시 끔은 창 분할까지 취소하는 설정이 아니라 같은 창을 다시 계산하는 대조입니다. [적용 범위·제약](../docs/guides/qwen3_optimization.md#부분-음성-창-캐시-실험)을 확인합니다.

## 토큰 후보 묶음 검증 진단

`token_draft.h`는 현재 Q8 greedy 생성에서 앞서 얻은 부분 토큰을 후보로 삼는 선택 상태 정책입니다. `edge_gguf_set_token_draft(handle, int32_tokens, count, block)`은 다음 한 호출에만 후보를 공급합니다. block은1(직렬)/2/4/8이며 잘못된 token ID·EOG 후보·길이 초과는 거부하고 이전 대기 후보도 지웁니다. 선택 설정이 없는 공유 실행은 setter를 호출하지 않아 직렬 생성합니다.

첫 토큰은 원래 전체 음성 prefill 뒤 Q8으로 선택해 즉시 배포합니다. 이후 확정된 토큰과 후보를 한 causal batch로 계산하고 각 위치의 Q8 greedy 결과를 순서대로 검사합니다. 불일치 이후 KV는 제거하고 Q8이 고른 토큰부터 직렬 생성합니다. 후보 자체를 자막으로 발행하거나 확정하지 않습니다. `token_draft_v1` capability와 별도 새 빌드 경로를 사용하고 기존 빌드는 보존합니다.

`native_result.token_draft`에 후보 수·묶음·제안/수락/거부 수를 기록합니다. `token_decode_calls`는 native decode 호출 횟수이고 `token_decode_evaluated_tokens`는 오검증으로 버린 행까지 포함한 실제 평가 토큰 수입니다. 미소비 후보는 취소/상한 등으로 남을 수 있습니다. 출력 token 수로 바꾸어 해석하지 않습니다. CPU의 다중 행 처리 비용과 수치 차이는 RP5에서 확인하며, 지연 개선을 가정하지 않습니다.

기존 benchmark의 요청 준비 hook으로 진단한 뒤, 선택 `final_token_draft_block`과 `qwen/token_drafts.py`로 실제 직전 부분 토큰 전달을 연결했습니다. 원래 전체 음성·Q8 정밀도·5초 확정·토큰 표시를 유지합니다. 공유 설정은 setter를 활성화하지 않습니다. 전체 PCM에서 원시 출력/CER 동등성은 성립하지 않았으며, 별도 [탐색적1배속 실측](../results/summaries/optimization/token-draft-stream-20261119.md)에 비용·지연·정확도 절충을 기록했습니다. 참고: [고정 llama.cpp 검증 예제](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/examples/speculative-simple/speculative-simple.cpp). 별도 draft 모델은 로드하지 않습니다.

## Hailo-8L 합성곱 실험 재현

Host의 DFC 3.x는 x86-64 Linux의 별도 Python 3.10 환경에 둡니다. 모델의 실제 특징 계수 및 입출력 캡처에는 위 GGUF 빌드에 `--profile-operators --capture-boundaries`를 추가하고 `EDGE_ASR_QWEN_BOUNDARY_DIR`로 비어 있는 호출별 폴더를 지정합니다. Q8 해시 검사, 정적 ONNX 내보내기, DFC 설정, 실제 HEF 식별자는 [2026-11-20 장치 실측](../results/summaries/optimization/hailo8l-q8-20261120.md)에 있습니다.

다음은 RP5에서 원래 엔진에 선택 라이브러리를 빌드한 명령입니다. `trial`은 HailoRT 4.23.0 헤더와 공유 라이브러리를 푼 실험 경로이며 `-o`의 대상은 새 파일로 지정합니다.

```bash
engine="$PWD/runtimes/qwen3-opt/llama-q8-20261109-01"
trial="$PWD/runtimes/qwen3-opt/hailo8l-q8-20261120-01"
sdk="$trial/device/unpack/usr"
g++ -std=c++17 -O2 -shared -fPIC -Wall -Wextra -Werror \
  -isystem "$sdk/include" -I "$engine/src/ggml/include" native/gguf/hailo_conv.cpp \
  -L "$sdk/lib" -l:libhailort.so.4.23.0 -L "$engine/build/bin" -lggml-base -ldl -pthread \
  -Wl,-rpath,"$sdk/lib:$engine/build/bin" -o "$trial/hybrid-rebuild.so"
```

## 토큰 선택 점수 진단

`build.py --trace-tokens`는 별도 경로에 `token_trace_v1` 빌드를 만듭니다. 기본 빌드에는 해당 코드·setter가 들어가지 않습니다. 진단 setter `edge_gguf_set_token_trace(handle, enabled)`는 다음 한 호출에만 적용하며 잘못된 값은 대기를 지우고 거부합니다. `token_trace.h`는 EOG를 포함한 각 선택에서 원시 logits의 선택 토큰/가장 높은 경쟁 토큰·점수 차·출력 위치·batch 행·직전 decode 행 수·후보 수락/제거 상태를 최대 출력 상한만큼 기록합니다. 원시 logits·샘플러·KV·출력은 수정하지 않습니다.

점수 배열 순회와 JSON 기록 비용이 있어 진단 시간을 성능 값으로 사용하지 않습니다. 동일 빌드의 계측 끔/켬 전사·토큰 일치와 이전 빌드의 원시 출력을 먼저 확인합니다. 같은 출력 접두부의 첫 분기에서 점수 순위가 바뀌었는지 확인하기 위한 관측이며, 어느 커널/레이어가 수치 차이를 만들었는지까지 자동으로 규명하지 않습니다.
