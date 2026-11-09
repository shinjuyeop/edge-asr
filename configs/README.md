# 실행 설정

## 현재 고정100개 평가

| 설정 | 용도 |
| --- | --- |
| [fixed100_onnx_2t.toml](optimization/fixed100_onnx_2t.toml) | 기존 ONNX INT8 2T 출발 참조 |
| [fixed100_gguf_q8_2t.toml](optimization/fixed100_gguf_q8_2t.toml) | GGUF Q8_0 2T 자체 최적화 기준선 |
| [eval_manifest.example.jsonl](eval_manifest.example.jsonl) | manifest 형식만 보여 주는 예시2줄. 실제100개 음성·정답이 아님 |

[평가 기준](../docs/research/fixed100_baseline.md), [최신 실측](../results/summaries/optimization/fixed100-baseline-20261109.md), [실행 안내](../docs/guides/qwen3_optimization.md)를 따릅니다. 실제 입력은 로컬 `data/evaluation/edge100-20261109-01/manifest.jsonl`이며 고정 평가 순서는 별도 실행 경로에서 준비합니다.

## 이전 실험 재현

| 설정 | 당시 용도 |
| --- | --- |
| [opt0_onnx_2t.toml](optimization/opt0_onnx_2t.toml) | OPT-0 소규모 반복·오류 재현. [프로토콜](../docs/research/opt0_pilot.md) |
| [opt2_gguf_q8_2t.toml](optimization/opt2_gguf_q8_2t.toml) | OPT-2 최초 GGUF/KSS 비교. [비교 계약](../docs/research/opt2_gguf.md) |
| [legacy/](legacy/README.md) | 기존 세 모델 batch·Zip 1배속·M1 설정 |

설정 안의 상대 경로는 저장소 루트 기준입니다. CLI 기본 모델은 Zipformer이므로 Qwen 실행에는 설정을 명시합니다. 기존 설정을 덮어쓰지 않고 새 후보 설정·고유 실행 ID를 사용합니다.
