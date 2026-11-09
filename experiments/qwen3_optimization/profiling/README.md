# 기존 ORT CPU 바이너리 호출 계측

[OPT-1 프로토콜](../../../docs/research/opt1_profiling.md)의 별도 계측 라이브러리입니다. 선택된 CPU 파일 기반 Qwen 모델과 ORT 1.28.2/API28만 대상으로 하며 모델 연산·입출력·세션 옵션은 바꾸지 않습니다. 기존 Python 환경에 설치하지 않습니다.

공식 `microsoft/onnxruntime`의 `v1.28.2/include/onnxruntime/core/session/`에서 `onnxruntime_c_api.h`, `onnxruntime_error_code.h`, `onnxruntime_ep_c_api.h`를 별도 `runtimes/.../include/`에 준비합니다. 원본 URL은 다음과 같습니다.

- [C API](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_c_api.h)
- [error code](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_error_code.h)
- [EP API](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.28.2/include/onnxruntime/core/session/onnxruntime_ep_c_api.h)

헤더는 Microsoft의 MIT 라이선스와 각 원본 저작권 표시를 유지합니다. 빌드 스크립트가 확인한 헤더 SHA-256을 검사하고 컴파일러·명령·코드/헤더/바이너리 해시를 `build.json`에 남깁니다. output은 존재하지 않는 새 경로여야 합니다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  experiments/qwen3_optimization/profiling/build_trace.py \
  --headers runtimes/qwen3-opt/ort-trace-20261109-01/include \
  --output runtimes/qwen3-opt/ort-trace-20261109-01/rp5

EDGE_ASR_ORT_HEADERS=runtimes/qwen3-opt/ort-trace-20261109-01/include \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.optimization.test_profiling -v
```

실제 자식 실행에만 새 `libedge_asr_ort_trace.so`의 절대 경로를 LD_PRELOAD로 지정하고, 기존 환경의 `sherpa_onnx/lib/libonnxruntime.so` 절대 경로를 `EDGE_ASR_ORT_LIBRARY`로 지정합니다. 열/시간/메모리 guard 자체에는 preload하지 않습니다. `tools profile-qwen3 --help`와 프로토콜의 입력·반복·모드를 따릅니다.

고정 native event 버퍼를 사용하므로 각 프로세스의 동일 호출 스레드에서 순차 전사합니다. 여러 모델을 동시에 호출하는 서비스용 tracer가 아닙니다. 실제 TTFT 대신 prefill logits 준비와 다음 decode 진입 경계를 보고하며, 나머지 C++ 비용을 KV 복사 시간 하나로 단정하지 않습니다.
