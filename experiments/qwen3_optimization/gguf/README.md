# llama.cpp/libmtmd CPU 연결

[OPT-2 프로토콜](../../../docs/research/opt2_gguf.md)의 고정 소스·두 GGUF 자산을 사용합니다. 기존 wheel이나 시스템에 설치하지 않습니다. `bridge.cpp`는 모델을 상주시킨 batch C ABI이며 토큰 callback이나 청크 간 cache 재사용은 제공하지 않습니다.

RP5의 별도 CMake CPU 빌드 후 다음처럼 연결합니다. 출력은 새 폴더여야 합니다.

```bash
.venv/bin/python experiments/qwen3_optimization/gguf/build_bridge.py \
  --source runtimes/qwen3-opt/llama-q8-20261109-01/src \
  --engine-build runtimes/qwen3-opt/llama-q8-20261109-01/build \
  --output runtimes/qwen3-opt/llama-q8-20261109-01/bridge-rp5-01
```

`build.json`은 upstream commit·소스/바이너리/엔진 해시와 명령을 남깁니다. 공통 어댑터는 두 모델 파일과 연결 바이너리의 해시를 검사합니다. 실제 비교에는 `configs/optimization/opt2_gguf_q8_2t.toml`과 `tools benchmark-qwen3` 또는 공통 `evaluate`를 사용하고 반드시 `thermal-run` 안에서 실행합니다. `benchmark-qwen3`는 기존 반복 기준선 실행기의 별칭입니다.

Host의 `test_qwen3_gguf.py`는 C ABI 모의 호출·원문 보존·실패 처리와 선택적 C++ 문법 검사입니다. 실제 모델 성능 검증은 RP5 원시 결과가 있을 때만 표시합니다.
