# 파일·폴더 경로 이행

## 개발 진입 경로 정리 — 2026-11-11

| 이전 위치·동작 | 현재 위치·동작 |
| --- | --- |
| `src/edge_asr/benchmark.py` | `src/edge_asr/legacy/benchmark_zipformer.py` — M1 전용임을 명시 |
| `tests/common/test_benchmark.py` | `tests/legacy/test_benchmark_zipformer.py` |
| 최신 개발 TOML이 로컬 실험 폴더에만 존재 | `configs/gguf_realtime.toml`로 동일 TOML 값을 공유. 원본은 보존 |
| `tools list`가 모든 과거 명령을 함께 표시 | 기본은 현재 도구. `--group all`은 전체, `--group legacy`는 과거 재현 |
| `evaluate`가 설정 생략 시 Zipformer 선택 | `--config` 필수. 기존 Zip 평가에는 `--config configs/legacy/zipformer_eval.toml` 명시 |

`scripts/benchmark_zipformer.py`, `tools repeat-zipformer`, `live`, 기존 도구 별칭은 유지합니다. Python 내부의 옛 `edge_asr.benchmark` import는 새 위치로 갱신하며 전달 전용 wrapper는 두지 않습니다. 공통 해시는 계속 `artifacts.py`에서 가져옵니다. 일반 평가의 이전 모델 어댑터와 현재 Qwen 추론 동작은 유지합니다.

환경은 [환경 기록](environment.md), 개발·검사는 [CONTRIBUTING](../../CONTRIBUTING.md), 현재 명령은 [도구 안내](tooling.md)를 따릅니다. 과거 결과 JSON·소스 해시·자산 경로는 소급 변경하지 않습니다.

검증은 Host에서 수행했습니다. Git 추적 대상과 새 소스만 복사하고 임시 Git 커밋을 만든 자산 없는 작업 폴더에서 전체381개 중379개 통과·선택 native 헤더 검사2개 생략을 확인했습니다. Ruff 검사·포맷·M1 원본 해시·공유 설정의 원본 TOML 값 일치도 확인했습니다. 원시 로그는 `results/maintenance/team-cleanup-20261111-01/`에 있습니다. GitHub Actions 원격 실행과 RP5 실제 추론은 이번 검증에 포함하지 않습니다.

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
