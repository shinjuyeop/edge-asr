# 실행 설정

현재 Qwen 기준 설정은 두 개이며 양자화 후보는 하위 폴더로 구분합니다. 상대 자산 경로는 저장소 루트 기준이고 `asset_manifest`는 모델 폴더 기준입니다.

| 파일 | 용도 |
| --- | --- |
| [onnx.toml](onnx.toml) | ONNX INT8 2T 출발 참조 |
| [gguf.toml](gguf.toml) | GGUF Q8_0 2T 최적화 기준선 |
| [quantization/](quantization/) | text Q6_K·Q5_K_M·Q4_K_M 후보. 오디오 mmproj Q8·기존 런타임 유지 |
| [eval_manifest.example.jsonl](eval_manifest.example.jsonl) | 목록 형식 예시2줄. 실제 평가100개가 아님 |
| [legacy/](legacy/README.md) | 이전 세 모델·Zip 1배속·M1 재현 설정 |

[평가 기준](../docs/research/fixed100_baseline.md), [실측 결과](../results/summaries/optimization/fixed100-baseline-20261109.md), [실행 안내](../docs/guides/qwen3_optimization.md)를 따릅니다. 실제 음성 목록은 로컬 `data/evaluation/edge100-20261109-01/manifest.jsonl`입니다. 비교 순서는 `tools prepare-baseline100`으로 새 실행 경로에 준비합니다.

2026-11-10 경로 정리에서 두 현재 설정은 파일명만 바꾸고 내용·SHA를 유지했습니다. 중복 OPT-0/OPT-2 설정은 통합했으며 당시 파일은 commit `9957094`에 있습니다. 후보 실험에는 새 설정과 실행 ID를 사용하고 기준 설정을 덮어쓰지 않습니다. Qwen 실행에는 설정을 명시합니다.
