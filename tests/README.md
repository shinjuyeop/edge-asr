# 테스트

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
| 계측·열 | `test_runtime_diagnostics.py`, `test_thermal_policies.py`, `test_cooling.py`, `test_load_probe.py` | native/Python ORT 구분·CPU/RSS/TID·현재 제한/과거 이력·실패 영수증 |
| 구조·보존 | `test_project_structure.py`, `test_consolidate.py` | 의존 방향·지연 import·문서 링크·원시/환경 보존·통합 충돌/복원 |

Linux 프로세스 검사는 해당 환경에서, 합성 ffmpeg 변환 검사는 도구가 있을 때 실행한다. skip 수와 이유를 함께 읽는다. 실제 RP5 모델 실행과 원시 보존 검사는 [검증 기록](../results/summaries/README.md)에 별도로 남긴다. 리팩터링은 기존 원시의 재집계 일치와 CLI 호환성을 확인하고 새 실행은 별도 ID로 보존한다.
