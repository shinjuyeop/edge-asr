# 이전 모델 비교·자막 설정

`zipformer_eval.toml`, `sensevoice_eval.toml`, `qwen3_eval.toml`은 이전 batch 평가, `zipformer_paced.toml`은 Zip 1배속 입력 설정입니다. 파일 내용은 이동 전과 같습니다. `zipformer_streaming.toml`은 M1 탐색 설정 원본입니다.

기존 비교·자막 도구는 이 폴더를 사용합니다. 신규 Qwen 최적화에는 [별도 설정](../optimization/qwen3_onnx_int8.toml)을 명시합니다. 모델·원시 데이터 경로는 그대로 유지합니다.
