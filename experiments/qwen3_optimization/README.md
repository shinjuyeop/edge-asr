# Qwen3-ASR 최적화 실험

현재 로드맵은 [OPT-0~4](../../docs/research/qwen3_optimization.md), 진행 상태는 [status](../../docs/status.md)에서 관리합니다. 이 폴더는 새 연구의 일회성 분석·재현 실험을 위한 자리이며 기존 M1 원본과 구분합니다.

현재 재사용 가능한 Qwen 단독 smoke는 [tools/optimization/qwen3.py](../../src/edge_asr/tools/optimization/qwen3.py)에 있습니다. 아직 프로파일러나 GGUF 실행기를 구현하지 않았습니다. 구현이 생길 때 검증 범위와 실행법을 추가합니다.

새 실행에는 `opt0-onnx-<날짜>-<번호>`처럼 단계·조건·고유 ID를 사용하고 원시 결과는 `results/optimization/<단계>/<실행ID>/`에 보관합니다. 검토한 요약만 [optimization 요약](../../results/summaries/optimization/README.md)에 추가합니다. 환경·모델·외부 엔진 소스/빌드는 이 폴더 안에 넣지 않습니다.
