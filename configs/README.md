# 실행 설정

새 Qwen 개발은 `gguf_realtime.toml`에서 시작합니다. 기존 batch 기준선과 양자화 후보는 별도로 보존합니다. 상대 자산 경로는 저장소 루트 기준이고 `asset_manifest`는 모델 폴더 기준입니다.

| 파일 | 용도 |
| --- | --- |
| [gguf_realtime.toml](gguf_realtime.toml) | 현재 개발용 Q8·생성2T/음성·prefill3T. 토큰 표시·스레드 분리 bridge 필요 |
| [onnx.toml](onnx.toml) | ONNX INT8 2T 출발 참조 |
| [gguf.toml](gguf.toml) | GGUF Q8_0 2T 최적화 기준선 |
| [quantization/](quantization/) | text Q6_K·Q5_K_M·Q4_K_M 후보. 오디오 mmproj Q8·기존 런타임 유지 |
| [eval_manifest.example.jsonl](eval_manifest.example.jsonl) | 목록 형식 예시2줄. 실제 평가100개가 아님 |
| [legacy/](legacy/README.md) | 이전 세 모델·Zip 1배속·M1 재현 설정 |

[평가 기준](../docs/research/fixed100_baseline.md), [실측 결과](../results/summaries/optimization/fixed100-baseline-20261109.md), [실행 안내](../docs/guides/qwen3_optimization.md)를 따릅니다. 실제 음성 목록은 로컬 `data/evaluation/edge100-20261109-01/manifest.jsonl`입니다. 비교 순서는 `tools prepare-baseline100`으로 새 실행 경로에 준비합니다.

`gguf_realtime.toml`은 `results/optimization/opt3/realtime-split100-20261111-01/configs/split-2-3.toml`의 TOML 값을 그대로 공유 경로에 옮긴 개발 프로필입니다. 원본 실험 설정과 기존 `gguf.toml`·`onnx.toml`은 보존합니다. 자산이 없는 새 clone에서도 설정 검사는 가능하지만 실제 추론은 준비된 RP5 환경에서만 수행합니다.

이 파일은 모델·평가 계약을 담고 있습니다. `evaluation.mode = "batch"`를 마이크 또는 연속 입력 모드로 해석하지 않습니다. 연속 입력은 기존 `tools replay-qwen3`가 담당하며 VAD·부분 갱신·토큰 표시 옵션은 [현재 개발용 대표 명령](../docs/guides/qwen3_optimization.md#현재-개발용-대표-명령)에 함께 고정합니다. TOML을 선택하는 것만으로 이 CLI 옵션들이 활성화되지는 않습니다.

2026-11-10 경로 정리에서 기존 두 기준선은 파일명만 바꾸고 내용·SHA를 유지했습니다. 중복 OPT-0/OPT-2 설정은 통합했으며 당시 파일은 commit `9957094`에 있습니다. 후보 실험에는 새 설정과 실행 ID를 사용하고 기준 설정을 덮어쓰지 않습니다. Qwen 실행에는 설정을 명시합니다.
