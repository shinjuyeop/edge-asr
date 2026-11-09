# 테스트

`common/`은 재사용 기반, `optimization/`은 Qwen 어댑터·단독 도구, `legacy/`는 이전 비교·자막·병합을 검사합니다. 세 폴더를 모두 검색하는 기존 명령을 유지합니다.

Host에서 코드 계약을 확인하고 RP5에서 같은 테스트와 필요한 실제 추론을 검증한다. 단위 테스트는 가짜 ASR/VAD·임시 WAV·실제 spawn/큐를 사용한다. 외부 데이터를 다운로드하거나 RP5에 접속하지 않는다. 통과가 실제 모델 정확도·냉각·마이크·UI 성능을 뜻하지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
ruff check src tests scripts
ruff format --check src tests scripts
sha256sum -c experiments/m1_zipformer/SHA256SUMS
bash -n scripts/deploy_rp5.sh
```

| 영역 | 주요 테스트 | 확인하는 계약 |
| --- | --- | --- |
| 평가·어댑터 | `test_evaluation*.py`, `test_sensevoice.py`, `test_qwen3*.py`, `test_cli.py` | 모델1회 로딩·발화별 상태·미지원 모드·정규화/CER·재채점·실패 보존·Qwen 입력 예산/자산 고정/잘림 거부 |
| 입력·동시 실행 | `test_streaming.py`, `test_concurrent_probe.py`, `test_online_vad.py` | 독립1배속 입력·유한 큐·실제 드롭·PCM/원본 좌표·결과 수신·자식 회수 |
| 자막 | `test_captions.py` | 원문 분리·늦은 반환·중복/해시 오류·stdout 실패·저장 원시 재집계 |
| 병합 연구 | `test_merging.py`, `test_segment_merge.py`, `test_token_alignment.py`, `test_token_splice.py` | 정답 비의존·문자/음성 소유권·후보 보류·앵커/접두부 오류·복사 보존 |
| VAD·구간 | `test_segmentation.py`, `test_segmented_accuracy.py`, `test_vad*.py` | 문맥/구간 상한·연속 확률·실패·주석/미확정 정답·모델 없는 채점 |
| 데이터 | `test_kss_subset.py`, `test_public_vad*.py` 등 | 출처/해시·원본 보존·형식·보류 표본·결정적 파생 입력 |
| 계측·열 | `test_runtime_diagnostics.py`, `test_thermal_policies.py`, `test_cooling.py`, `test_load_probe.py` | native/Python ORT 구분·공통 계측 경계·예외 시 fd2 복원·CPU/RSS/TID·열 제한·실패 영수증 |
| 구조·보존 | `test_project_structure.py`, `test_consolidate.py` | 의존 방향·지연 import·문서 링크·원시/환경 보존·통합 충돌/복원 |

Linux 프로세스 검사는 해당 환경에서, 합성 ffmpeg 변환 검사는 도구가 있을 때 실행한다. skip 수와 이유를 함께 읽는다. 실제 RP5 모델 실행과 원시 보존 검사는 [검증 기록](../results/summaries/README.md)에 별도로 남긴다. 리팩터링은 기존 원시의 재집계 일치와 CLI 호환성을 확인하고 새 실행은 별도 ID로 보존한다.

ORT 계측의 native mock은 `EDGE_ASR_ORT_HEADERS`에 별도 공식 1.28.2 헤더 경로를 지정하고 g++가 있을 때 실행합니다. [빌드·검사 안내](../experiments/qwen3_optimization/profiling/README.md). 지정하지 않으면 해당 검사 1개만 이유와 함께 skip하며 Python 경계/실패 검사는 실행합니다. 실제 ASR 추론과 구분합니다.

GGUF의 선택적 C++ 문법 검사에는 `EDGE_ASR_LLAMA_SOURCE`를 고정 upstream 소스 경로로 지정합니다. 어댑터의 C ABI 모의 검사는 native 없이 실행하며 자산/바이너리 해시·같은 PCM 전달·원문 보존·잘림/반복/UTF-8 실패를 확인합니다. 실제 GGUF 로딩·정확도·처리 시간은 RP5 실행으로만 검증합니다.

`optimization/test_fixed100.py`는 고정 선정의 입력 순서 독립성·화자 할당량·정규화 중복 제외·할당량 부족 거부·원본 해시·tar 경로/링크·FLAC 형식 검사를 수행합니다. 기존 ffmpeg가 있으면 합성 PCM의 실제 변환·원음 보존·프레임 불일치 거부도 확인합니다. 공개 데이터 다운로드나 ASR 추론은 하지 않습니다. 실제100개 파일의 준비·검증은 [별도 기록](../results/summaries/optimization/fixed100-preparation-20261109.md)에 남깁니다.
