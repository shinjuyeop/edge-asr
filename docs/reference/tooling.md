# 실행 도구와 명령 이행표

공통 평가는 `edge-asr evaluate/score`를 사용하고, 자료 준비·진단·재채점은 `edge-asr tools <명령>`으로 실행한다. 기존 RP5 환경에는 패키지를 재설치하지 않는다.

```bash
.venv/bin/python scripts/edge_asr.py tools list
.venv/bin/python scripts/edge_asr.py tools probe-annotated-vad --help
# 설치된 환경에서는 같은 명령
edge-asr tools list
# PYTHONPATH=src인 개발 환경에서는 python -m edge_asr.tools도 지원
```

`tools`는 고정된 명령 목록에서 선택한 모듈만 import한다. 외부 플러그인 검색이나 모델 자동 다운로드는 없다. 모듈은 `src/edge_asr/tools/`에서 데이터 준비·측정·집계·유지보수로 나뉜다. 명령별 옵션은 이전과 동일하며, 과거 보고서의 `scripts/*.py` 직접 호출은 아래 명령으로 옮겨 사용한다. 기존 파일의 확장자를 뺀 이름이 명령 별칭으로도 동작하지만 새 문서에서는 표준 이름만 사용한다.

| 이전 파일 | 현재 명령 | 구현 모듈 |
| --- | --- | --- |
| `scripts/prepare_korean_test0.py` | `edge-asr tools prepare-official` | `data.official_sample` |
| `scripts/prepare_kss_subset.py` | `edge-asr tools prepare-kss` | `data.kss` |
| `scripts/prepare_m23_data.py` | `edge-asr tools prepare-external` | `data.external` |
| `scripts/prepare_m30_audio.py` | `edge-asr tools prepare-volume` | `data.volume` |
| `scripts/prepare_vad_cases.py` | `edge-asr tools prepare-vad` | `data.vad_cases` |
| `scripts/prepare_segmentation_cases.py` | `edge-asr tools prepare-segments` | `data.segmentation_cases` |
| `scripts/prepare_public_vad_review.py` | `edge-asr tools prepare-public-review` | `data.public_review` |
| `scripts/materialize_public_vad_review.py` | `edge-asr tools materialize-review` | `data.review_audio` |
| `scripts/prepare_vad_annotations.py` | `edge-asr tools prepare-annotations` | `data.vad_annotations` |
| `scripts/review_vad_annotations.py` | `edge-asr tools review-annotations` | `data.review_annotations` |
| `scripts/compare_models.py` | `edge-asr tools compare` | `probes.comparison` |
| `scripts/m30_thermal_run.py` | `edge-asr tools thermal-run` | `probes.thermal` |
| `scripts/m30_load_probe.py` | `edge-asr tools probe-load` | `probes.load` |
| `scripts/m30_preprocessing_probe.py` | `edge-asr tools probe-preprocessing` | `probes.preprocessing` |
| `scripts/m30_vad_probe.py` | `edge-asr tools probe-vad` | `probes.vad` |
| `scripts/m30_segment_probe.py` | `edge-asr tools probe-segments` | `probes.segments` |
| `scripts/m30_annotated_vad_probe.py` | `edge-asr tools probe-annotated-vad` | `probes.annotated_vad` |
| `scripts/summarize_comparison.py` | `edge-asr tools summarize-comparison` | `reports.comparison` |
| `scripts/summarize_m30.py` | `edge-asr tools summarize-preflight` | `reports.preflight` |
| `scripts/summarize_vad_probe.py` | `edge-asr tools summarize-vad` | `reports.vad` |
| `scripts/summarize_segment_probe.py` | `edge-asr tools summarize-segments` | `reports.segments` |
| `scripts/summarize_annotated_vad.py` | `edge-asr tools summarize-annotated-vad` | `reports.annotated_vad` |
| `scripts/consolidate_rp5.py` | `edge-asr tools consolidate` | `maintenance.consolidate` |

기존 `scripts/edge_asr.py`, `scripts/benchmark_zipformer.py`, `scripts/deploy_rp5.sh`는 각각 공통 CLI·M1 반복 도구 호환 진입점·선택적 새 경로 복사를 위해 유지한다. 평소 소스 동기화는 main push/pull을 사용한다.

중복 `scripts/streaming_probe.py`는 바이트가 같은 `experiments/m1_zipformer/streaming_probe.py`로 합쳤다. 참고용 `online-decode-files.py`는 `experiments/m1_zipformer/reference/`에 원본 그대로 보관한다. `configs/legacy/zipformer_streaming.toml`은 원본 프로브에 자동 적용되지 않는 M1 탐색 설정이며 새 평가에는 `configs/zipformer_eval.toml` 또는 `zipformer_paced.toml`을 사용한다.

과거 JSON 영수증의 실행 명령·소스 해시와 측정값은 당시 상태를 나타내므로 소급 변경하지 않는다. 이전 소스 자체가 필요한 경우 해당 기록의 Git 커밋을 별도 읽기 전용 체크아웃에서 확인한다. 현재 RP5 자산/환경을 과거 버전으로 되돌리지 않는다.
