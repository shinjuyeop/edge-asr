# 파일·폴더 경로 이행

## 현재 구조 정리 — 2026-11-10

정리 전 main은 `9957094`입니다. 현재 Qwen 작업에 쓰는 Python·C++·데이터 준비를 역할별로 모았습니다. Python 내부 import 경로는 새 위치로 바뀌며 이전 경로의 중복 파일이나 wrapper는 두지 않습니다.

| 이전 위치 | 현재 위치 |
| --- | --- |
| `src/edge_asr/adapters/qwen3.py` | `src/edge_asr/qwen/onnx.py` |
| `src/edge_asr/adapters/qwen3_gguf.py` | `src/edge_asr/qwen/gguf.py` |
| `src/edge_asr/tools/optimization/baseline.py` | `src/edge_asr/qwen/benchmark.py` |
| `src/edge_asr/tools/optimization/profiling.py` | `src/edge_asr/qwen/profiling.py` |
| `experiments/qwen3_optimization/gguf/` | `native/gguf/` — `bridge.cpp`, `build.py` |
| `experiments/qwen3_optimization/profiling/` | `native/ort_trace/` — 계측 C++·빌드 |
| `experiments/qwen3_optimization/prepare_*.py` | `src/edge_asr/tools/data/` — CLI 준비 도구 |
| `configs/optimization/fixed100_*_2t.toml` | `configs/onnx.toml`, `configs/gguf.toml` — 내용·SHA 유지 |
| `configs/optimization/opt0_onnx_2t.toml`, `opt2_gguf_q8_2t.toml` | 현재 두 설정에 통합. 당시 파일은 정리 전 commit에 보존 |
| `tests/optimization/` | `tests/qwen/` |

`benchmark-qwen3`, `profile-qwen3`, `opt0-baseline`의 인자는 유지합니다. 준비 명령은 `tools prepare-fixed100`, `tools prepare-baseline100`, `tools prepare-opt0`로 통합했습니다. [실행 안내](../guides/qwen3_optimization.md). OPT-0의 옛 기본 출력 폴더는 달랐으므로 재현에는 당시 commit 또는 명시적인 새 `--output`을 사용합니다.

과거 영수증·원시/요약 JSON의 명령·설정 경로·소스 해시를 소급 변경하지 않습니다. 과거 Markdown의 이동 파일 링크만 현재 위치 또는 당시 Git 파일로 연결합니다. 정확한 옛 명령·import를 재현하려면 해당 기록의 소스 commit을 사용합니다.

기존 `.venv*`, 패키지·모델·원음·원시 결과·외부 엔진/빌드·`experiments/m1_zipformer/` 원본은 이동하거나 재설치하지 않습니다. 빌드 결과는 계속 `runtimes/`의 별도 ID에 둡니다.

## 이전 이행

2026-11-09의 모델 비교·자막 코드는 `legacy/`, 설정은 `configs/legacy/`, 연구 문서는 `docs/archive/model-comparison/`에 분리했습니다. 초기 `probe-qwen3`와1T 시작 설정은 이후 공통 코드 정리에서 폐기했으며 원본은 commit `f88b33f`에 있습니다. [당시 구조 검증](../../results/summaries/optimization/restructure-20261109.md), [공통 코드 정리](../../results/summaries/optimization/refactor-core-20261109.md).

현재 의존 방향은 [아키텍처](architecture.md), 최신 진행은 [status](../status.md), 전체 문서는 [문서 색인](../README.md)에서 확인합니다.
