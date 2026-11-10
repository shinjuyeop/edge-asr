# C++ 연결·빌드 도구

Python 실행 코드는 [src/edge_asr/qwen/](../src/edge_asr/qwen/), C++ 소스와 빌드는 이 폴더에 둡니다. 외부 llama.cpp·ORT 헤더와 빌드 결과는 로컬 `runtimes/`에 보관하며 기존 Python 환경에 설치하지 않습니다.

| 위치 | 역할 |
| --- | --- |
| [gguf/bridge.cpp](gguf/bridge.cpp) | llama.cpp/libmtmd batch C ABI. 모델 상주·KV 초기화·오디오/prefill·토큰 decode |
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

현재 검증 구성은 CPU native·GPU OFF·KleidiAI OFF입니다. 모델1회 로딩·발화별 KV 초기화 방식이고 토큰 callback·청크 간 cache 재사용은 미구현입니다. [Python 어댑터](../src/edge_asr/qwen/gguf.py)는 가중치와 연결 라이브러리의 해시를 확인합니다.

양자화 준비는 별도 환경에서 upstream `gguf-py`의 numpy·pyyaml·tqdm·requests 의존성을 준비하고, 고정 llama.cpp 소스의 `llama-quantize`를 새 빌드 경로에 만듭니다. 기존 추론 환경·엔진은 변경하지 않습니다. `quantize.py --help`의 source/tool/llama-source/audio-model/output을 지정하며 output은 새 폴더여야 합니다. 모델별 명세는 원본·도구·GGUF type·tensor dtype·SHA를 보존하고 Q8 오디오 파일은 기존 경로를 참조합니다. [실험 조건](../docs/research/qwen_realtime.md).

## ORT 계측

[OPT-1 프로토콜](../docs/research/opt1_profiling.md)을 따릅니다. 공식 v1.28.2의 [C API](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_c_api.h), [error code](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_error_code.h), [EP API](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_ep_c_api.h) 헤더를 별도 `runtimes/.../include/`에 준비합니다. MIT 라이선스·원본 저작권 표시를 유지하며 빌드 도구가 고정 헤더 SHA-256을 검사합니다.

```bash
.venv/bin/python native/ort_trace/build.py \
  --headers runtimes/qwen3-opt/ort-trace-20261109-01/include \
  --output "runtimes/qwen3-opt/<새빌드ID>/trace"
```

실제 **자식** 실행에만 trace 라이브러리 절대 경로를 `LD_PRELOAD`로, 기존 환경의 `libonnxruntime.so` 절대 경로를 `EDGE_ASR_ORT_LIBRARY`로 지정합니다. guard에는 preload하지 않습니다. 동일 프로세스에서 순차 계측하며 `tools profile-qwen3 --help`의 on/off/ABBA 옵션을 사용합니다. 내부 logits 시각은 사용자 TTFT와 구분합니다.

Host/RP5 모의·문법 검사는 [tests/qwen/](../tests/qwen/)에 있습니다. `EDGE_ASR_LLAMA_SOURCE`와 `EDGE_ASR_ORT_HEADERS`를 지정하면 고정 헤더의 C++ 검사도 실행합니다. 실제 모델 성능은 RP5 실측으로만 확인합니다.
