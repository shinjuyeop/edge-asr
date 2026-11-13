# 현재 개발·추론 환경

2026-11-11 KST에 Host 셸과 SSH로 접속한 RP5에서 직접 확인했습니다. 확인 당시 양쪽 소스는 `258a6503c913a9c5e6aef388278691169c2a4de3`입니다. 이 문서는 설치 상태의 스냅샷이며 새 환경의 설치 성공이나 모델 성능을 보증하는 lock 파일은 아닙니다.

## 실행 방식

현재 프로젝트는 **Docker 없이 장비별 Python 가상환경과 별도 native 빌드**를 사용합니다. 저장소에 Dockerfile·Compose 설정이 없고, 확인한 두 셸에서도 `/.dockerenv`·`/run/.containerenv`는 없었습니다. Host의 IDE 바깥 운영체제나 다른 프로젝트의 컨테이너 사용 여부는 이 기록의 범위가 아닙니다.

- Host: 구현·모델 없는 테스트·데이터 준비·결과 분석·영상 생성.
- RP5: ARM64 실제 모델 추론·지연·메모리·온도 측정.
- GGUF: Python `ctypes` → 프로젝트 C++ bridge → llama.cpp/libmtmd.
- ONNX: sherpa-onnx 내부 native ORT. VAD용 Python `onnxruntime`은 별도 런타임.

## 장비와 도구

| 항목 | Host에서 관측 | RP5에서 관측 |
| --- | --- | --- |
| 작업 폴더 | `/d/shin/edge-asr` | `/home/deepet/projects/edge-asr` |
| OS | Ubuntu 22.04.5 LTS | Debian GNU/Linux 13.3, trixie |
| 커널 | `6.8.0-138-generic` | `6.12.62+rpt-rpi-2712` |
| 아키텍처 | x86_64 | aarch64 |
| CPU/장비 | Intel Core i7-11700, 논리 CPU 16개 | Raspberry Pi 5 Model B Rev 1.0, Cortex-A76 4개 |
| `/proc/meminfo`의 MemTotal | 32,584,032 kB | 8,252,368 kB |
| 프로젝트 `.venv` Python | 3.11.14 | 3.11.18 |
| 셸 기본 Python | `/usr/bin/python` 3.10.12 | `python3` 3.13.5 |
| Git | 2.34.1 | 2.47.3 |
| Ruff | 0.14.14, Host 사용자 도구 | PATH에서 발견되지 않음 |
| ffmpeg | 4.4.2-0ubuntu0.22.04.1 | 7.1.5-0+deb13u1+rpt2 |
| g++ | Ubuntu 11.4.0 | Debian 14.2.0-19 |
| CMake | 4.3.2 | 3.31.6 |
| uv | 0.9.15 | PATH에서 발견되지 않음 |

명령은 `.venv/bin/python`처럼 **대상 환경의 Python을 명시**합니다. Host의 기본 Python 3.10은 프로젝트의 Python 3.11 이상 요구를 충족하지 않습니다. RP5에서도 기본 `python3`와 실제 추론 Python은 다릅니다. 시스템 Python을 교체하거나 Host 가상환경을 ARM64로 복사하지 않습니다.

## 가상환경별 용도

아래 경로는 각 장비의 저장소 루트 기준입니다. 같은 `.venv`라는 이름이어도 서로 다른 환경입니다.

| 장비·환경 | Python | 역할·주요 패키지 |
| --- | --- | --- |
| Host `.venv` | 3.11.14 | 코드·모의 검사. edge-asr 0.1.0, pip 24.0, setuptools 79.0.1. 추론 패키지 없음 |
| Host `.venv-qwen-inspect-20261106-01` | 3.11.14 | ONNX 자산 검사. onnx 1.19.1, numpy 2.4.6, ml_dtypes 0.6.0, protobuf 7.36.2, typing_extensions 4.16.0 |
| Host `.venv-gguf-quant-20261110-01` | 3.11.14 | GGUF 양자화 준비. numpy 2.4.6, PyYAML 6.0.3, tqdm 4.70.1, requests 2.34.2, certifi 2026.10.23, charset-normalizer 3.5.2, idna 3.20, urllib3 2.8.0 |
| RP5 `.venv` | 3.11.18 | 현재 GGUF·VAD 실행, ONNX 비교도 가능. 전체 목록은 아래 |
| RP5 `.venv-qwen3-onnx-20261106-01` | 3.11.18 | 보존한 ONNX 전용 환경. numpy 2.4.6, sherpa-onnx/core 2.0.1, pip 26.2.2, setuptools 79.0.1 |
| RP5 `.venv-qwen3-vad-20261106-01` | 3.11.18 | 보존한 ONNX+VAD 환경. 위 ONNX 환경 + onnxruntime 1.31.1, flatbuffers 25.12.19, packaging 26.3, protobuf 7.36.2, typing_extensions 4.16.0 |

RP5의 현재 `.venv`에서 `importlib.metadata`로 확인한 전체 배포 패키지 목록입니다. 이 환경에는 pip가 없어 `pip freeze`에 의존하지 않았습니다.

```text
cffi==2.1.1
flatbuffers==25.12.19
numpy==2.4.6
onnxruntime==1.31.1
packaging==26.3
protobuf==7.36.2
pycparser==3.12
sherpa-onnx-core==2.0.1
sherpa_onnx==2.0.1
sounddevice==0.5.6
soundfile==0.14.0
typing_extensions==4.16.0
```

2026-11-11 스냅샷 당시 입력은 WAV였습니다. 프로젝트는 RP5에서 재설치 없이 `scripts/edge_asr.py`로 체크아웃의 `src/`를 사용합니다. `env --probe-runtime`으로 sherpa native ORT **1.28.2**도 확인했으며 해당 환경 조사에서는 모델을 로딩하지 않았습니다.

## USB 입력 추가 확인 — 2026-11-13

기존 `arecord`·ALSA를 사용해 마이크 입력을 추가했습니다. 연결 장치는 card2 `USB Audio Device`, 선택 ID는 `plughw:CARD=Device,DEV=0`입니다. 하드웨어는 모노·S16_LE와48000/44100/32000/22050/11025/8000Hz를 광고했고16kHz 직접 열기는 거부됐습니다. `plughw` 변환으로16kHz 수집·실제 Qwen/웹 자막을 확인했습니다. 기존 Python 패키지·오디오 기본 설정·모델은 바꾸지 않았습니다. [사용법](../guides/listener.md#rp5-usb-마이크), [실측·자산 해시](../../results/summaries/optimization/microphone-web-20261113.md).

## 보존한 2026-11-11 GGUF 자산·빌드

아래 표는 최초 확인한 split 빌드입니다. 이후 현재 개발 설정 [configs/gguf_realtime.toml](../../configs/gguf_realtime.toml)은 별도 `prefix-20261112-01/bridge/` 빌드를 사용하며 기존 split 빌드는 보존합니다. 최신 해시는 각 실행의 `model_files`·`model_metadata.runtime_build`에 기록합니다. 모델·native 바이너리는 Git에서 제외하며 [빌드 안내](../../native/README.md)와 [실행 안내](../guides/qwen3_optimization.md)를 함께 따릅니다.

| 항목 | 확인한 값·위치 |
| --- | --- |
| 모델 | Qwen3-ASR-0.6B, text Q8_0 + audio mmproj Q8_0 |
| 가중치 경로 | `models/qwen3-opt/gguf-q8-20261109-01/` |
| 외부 엔진 소스·빌드 | `runtimes/qwen3-opt/llama-q8-20261109-01/{src,build}/` |
| llama.cpp 커밋 | `7d7f0287530bc8981690f361d8c1627dbae1c99a` |
| 현재 bridge | `runtimes/qwen3-opt/split-20261111-01/bridge/libedge_asr_gguf.so` |
| 빌드 명세 | 같은 bridge 폴더의 `build.json` |
| 빌드 대상·설정 | ARM64 native CPU, GPU OFF, KleidiAI OFF |
| bridge 컴파일 | g++ 14.2.0, C++17, `-O2 -fPIC -shared -Wall -Wextra -Werror` |
| 선택 기능 | `token_callback_v1`, `split_threads_v1` |
| bridge SHA-256 | `d50cc73207a79378ee03b9d0da04f1a02ad8366671c8abb29e2e6261deb06728` |

bridge 해시는 RP5 파일을 직접 읽어 `build.json`과 대조했습니다. 엔진 세부 명령·라이브러리 해시·CMake cache 해시는 그 빌드 명세에 있습니다. 이 바이너리는 해당 RP5 엔진 경로를 rpath로 사용하므로 다른 장비·경로에서는 소스와 빌드 조건을 맞춰 별도로 빌드하고 새 설정에 연결합니다. 기존 바이너리는 덮어쓰지 않습니다.

Python 패키지 목록만으로 GGUF 환경이 재현되지는 않습니다. **Python 환경 + 외부 엔진 커밋/빌드 + 모델/음원 해시 + 실행 설정**을 함께 맞춥니다. 현재 Git 설정은 기존 자산 경로를 가리키며 자동 다운로드·자동 빌드를 하지 않습니다.

## 환경 기록 갱신

이번 원시 스냅샷은 Host의 `results/maintenance/team-cleanup-20261111-01/{host,rp5}-environment.json`에 보관했습니다. OS·CPU·각 가상환경의 패키지 목록·빌드 명세를 읽었고 패키지 설치·업데이트나 모델 추론은 수행하지 않았습니다.

환경을 실제로 바꾼 뒤에는 해당 장비에서 다음 정보를 새 실행 폴더에 기록하고 이 문서의 확인일·변경 항목을 갱신합니다. Python 배포 패키지 확인은 pip가 없어도 가능합니다.

```bash
.venv/bin/python scripts/edge_asr.py env
.venv/bin/python -c 'from importlib.metadata import distributions; print("\n".join(sorted({d.metadata["Name"] + "==" + d.version for d in distributions()})))'
uname -srmo
cat /etc/os-release
git rev-parse HEAD
```

native ORT 버전 확인이 필요할 때만 `env --probe-runtime`을 사용합니다. native 빌드를 바꾸면 새 `build.json`과 바이너리 해시도 기록합니다. SSH 키·인증 토큰·환경변수 전체는 공유 문서에 넣지 않습니다. 환경 준비 절차는 [설치 안내](../guides/setup.md)를 따릅니다.

## 연산 진단 bridge 추가 — 2026-11-13

별도 `runtimes/qwen3-opt/operators-20261113-01/bridge/`를 빌드했습니다. 기존 고정 engine6개 SHA는 모두 같고 기존 운영용 prefix bridge·가상환경/패키지·모델은 그대로입니다. `operator_profile_v1`과 명시적 모드 전환으로 공개 LLM/mtmd node callback만 켭니다. per-node 동기화의 영향을 기존/계측 끔/켬으로 비교했으며 운영 빌드로 채택하지 않았습니다. [빌드/계측 범위](../../native/README.md#gguf-연산별-진단), [RP5 실제 결과](../../results/summaries/optimization/operator-profile-20261113.md).

## Q4 repack·스레드 대조 — 2026-11-13

별도 `runtimes/qwen3-opt/q4-norepack-20261113-01/{engine,bridge}/`를 빌드해 기존 repack ON과 비교했습니다. CPU C/C++ flags는 같고 repack define만 빠졌으며 기존 엔진6개·모델·패키지는 보존했습니다. OFF는 느려져 제외했습니다. 선택된 Q4 음성/prefill4T는 **기존 prefix bridge/엔진**을 그대로 사용합니다. Q8 공유 기본과 기존 Q4 3T는 유지합니다. [실측·시작 온도·CPU 비용](../../results/summaries/optimization/prefill-q4-20261113.md).

## Q4 3T 커널 추적 추가 — 2026-11-13

`runtimes/qwen3-opt/kernel-q4-20261113-01/libedge_kernel_trace.so`를 별도 빌드했습니다. 추론 자식에만 preload하며 기존 bridge·엔진·패키지·가중치는 그대로입니다. 실제 repack GEMM/GEMV·활성값 변환 호출을 기록하고 끔/켬의 전사·token ID·계측 비용을 대조했습니다. 운영 기본에는 로딩하지 않습니다. 사용자 결정으로 Q4 후속은2/3T를 유지하고4T는 보존한 실험으로 둡니다. [실제96호출·온도/클럭·해시 기록](../../results/summaries/optimization/kernel-q4-20261113.md).
