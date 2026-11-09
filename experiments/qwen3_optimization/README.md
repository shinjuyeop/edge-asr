# Qwen3-ASR 최적화 실험

현재 로드맵은 [OPT-0~4](../../docs/research/qwen3_optimization.md), 진행 상태는 [status](../../docs/status.md)에서 관리합니다. 이 폴더는 새 연구의 일회성 분석·재현 실험을 위한 자리이며 기존 M1 원본과 구분합니다.

현재 재사용 가능한 Qwen 단독 smoke는 [tools/optimization/qwen3.py](../../src/edge_asr/tools/optimization/qwen3.py)에 있습니다. [profiling/](profiling/README.md)에 기존 ORT 호출 계측을, [gguf/](gguf/README.md)에 별도 llama.cpp/libmtmd CPU 연결을 둡니다. [GGUF 비교 프로토콜](../../docs/research/opt2_gguf.md)에서 실제 실행 전 입력·조건·판정 기준을 고정합니다.

새 실행에는 `opt0-onnx-<날짜>-<번호>`처럼 단계·조건·고유 ID를 사용하고 원시 결과는 `results/optimization/<단계>/<실행ID>/`에 보관합니다. 검토한 요약만 [optimization 요약](../../results/summaries/optimization/README.md)에 추가합니다. 환경·모델·외부 엔진 소스/빌드는 이 폴더 안에 넣지 않습니다.

첫 입력 준비는 `prepare_opt0.py`로 수행합니다. KSS 길이 표본과 기존 오류 PCM을 해시로 검증하고 새 경로에만 기록합니다. [실행 프로토콜](../../docs/research/opt0_pilot.md).

새100개는 `prepare_fixed100.py`로 공식 Zeroth/FLEURS 원본에서 고정 선정합니다. [출처·할당량·원본 검증·준비 명령](../../docs/research/fixed100.md)을 따릅니다. 다운로드와 PCM 준비는 ASR 실측과 구분합니다.

같은100개의 실행 순서는 `prepare_baseline100.py`가 원본을 보존하고 새 결과 경로에 해시 셔플로 준비합니다. `PYTHONPATH=src:.`와 `--output <새경로>`를 사용합니다. [출발 기준선·정확도/열 판정](../../docs/research/fixed100_baseline.md).
