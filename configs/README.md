# 실행 설정

| 경로 | 용도 |
| --- | --- |
| [optimization/qwen3_onnx_int8.toml](optimization/qwen3_onnx_int8.toml) | Qwen 최적화 시작 설정. 기존 1T ONNX 정책·모델 재사용, 출력 경로 분리; 기준선 확정 전 |
| [legacy/](legacy/README.md) | 기존 세 모델 batch·Zip 1배속·M1 설정 원본 |
| `eval_manifest.example.jsonl` | 공통 평가 manifest 형식 예시 |

설정 경로는 저장소 루트 기준입니다. 기존 CLI 기본 모델은 Zipformer로 유지하고 신규 OPT 명령에서 설정을 명시합니다. [공통 평가](../docs/guides/evaluation.md), [구조 이행](../docs/reference/structure_migration.md)을 참고하세요.
