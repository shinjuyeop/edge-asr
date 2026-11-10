# 외부 추론 엔진

llama.cpp 등 외부 엔진의 소스·빌드·바이너리는 `runtimes/qwen3-opt/<엔진ID>/`에서 별도로 관리합니다. OPT-2의 RP5 CPU 빌드는 `qwen3-opt/llama-q8-20261109-01/`의 `src/`, `build/`, `bridge-rp5-01/`로 분리했습니다. [빌드 안내](../native/README.md), [비교 계약](../docs/research/opt2_gguf.md). 이 README 이외 내용은 Git에서 제외합니다.

소스 URL·고정 커밋, 컴파일러/플래그, ARM64 CPU 옵션, 바이너리 해시를 실행 영수증에 기록합니다. 기존 Python 환경과 모델을 갱신하지 않습니다. GGUF 가중치와 mmproj는 별도 `models/qwen3-opt/gguf-q8-20261109-01/` 자산 경로에 보관합니다. ARM64 실행 바이너리를 Host native 추론 결과로 취급하지 않습니다.
