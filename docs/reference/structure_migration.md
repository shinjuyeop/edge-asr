# Qwen 최적화 중심 구조 이행 — 2026-11-09

이 개편은 기존 동작을 보존하며 연구 단계를 구분합니다. 기존 `main` 이력을 유지하고 이전 소스 기준은 `4e5dbed`입니다. 과거 영수증의 명령·소스 커밋·해시는 당시 기록으로 남깁니다.

| 이전 위치 | 현재 위치·역할 |
| --- | --- |
| `src/edge_asr/realtime/` | `src/edge_asr/legacy/realtime/` — Zip+VAD+Refine·자막 |
| `merging.py`, `segment_merge.py`, `token_alignment.py`, `token_splice.py`, `vad_probe.py` | `src/edge_asr/legacy/` — 과거 병합·진단 |
| `tools/live.py`, 비교/VAD probe·보고서·전용 데이터 준비 | `src/edge_asr/legacy/tools/` |
| `tools/probes/qwen3.py` | `src/edge_asr/tools/optimization/qwen3.py` — 기존 Qwen smoke |
| `configs/*_eval.toml`, `zipformer_paced.toml` | `configs/legacy/` — 파일 내용 그대로 유지 |
| 신규 Qwen 설정 | `configs/optimization/qwen3_onnx_int8.toml` — 기존 정책, 새 출력 위치 |
| `docs/research/`의 이전 계획 | `docs/archive/model-comparison/` |
| 이전 `docs/status.md` | `docs/archive/model-comparison/status-20261106.md` — 전환 전 상태 |
| 자막/Zip 1배속 가이드 | `docs/guides/legacy/` |
| flat 테스트 | `tests/common/`, `tests/optimization/`, `tests/legacy/` |

공통 어댑터·PCM·평가·채점·계측·VAD·열 관리와 데이터 준비는 기존 공통 위치에서 재사용합니다. `benchmark.py`와 `scripts/benchmark_zipformer.py`는 초기 반복 프로브 호환을 위해 유지합니다. Qwen ONNX 어댑터를 이전 모델 비교와 OPT가 함께 사용하며 복제하지 않습니다.

## 기존 실행의 호환 범위

`edge-asr`와 `scripts/edge_asr.py`의 명령 이름·인자·기본 모델은 유지합니다. `live`는 이전 Zip+Refine 실행이고 Qwen 단독 스트리밍을 뜻하지 않습니다. 도구 이름은 유지하되 dispatch와 내부 Python import 경로가 바뀝니다. 개별 Python 모듈을 직접 import한 외부 코드는 위 경로에 맞춰 수정해야 합니다.

설정을 직접 지정한 명령은 예를 들어 `--config configs/qwen3_eval.toml`을 `--config configs/legacy/qwen3_eval.toml`로 바꿉니다. 예전 경로의 중복 파일이나 symlink는 두지 않습니다. 신규 OPT는 `configs/optimization/qwen3_onnx_int8.toml`을 명시합니다. 이전 명령을 바이트 단위로 재현하려면 당시 Git 커밋을 사용합니다.

`.venv*`, 패키지, 모델, 원본 데이터, 기존 원시·요약 JSON, `experiments/m1_zipformer/` 원본은 이동·재설치·덮어쓰지 않습니다. 이전 결과는 기존 링크와 실행 ID를 유지하고 새 요약만 `results/summaries/optimization/`로 구분합니다.

## 개발 경계

공통 코드와 새 최적화 도구는 `legacy` 구현에 의존하지 않습니다. CLI의 이전 명령 분기만 예외입니다. `legacy`는 공통 기반을 재사용하며 보고서는 실행 probe에 의존하지 않습니다. 기존 자막의 입력/반환 코드도 병합 실험 모듈을 역으로 import하지 않습니다. 테스트로 이 경계를 확인합니다.

[아키텍처](architecture.md), [테스트](../../tests/README.md), [활성 연구](../research/qwen3_optimization.md)를 참고하세요.
