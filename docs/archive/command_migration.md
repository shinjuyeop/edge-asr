# 과거 scripts 명령 이행표

2026-10-29 구조 정리 때 보존한 명령 매핑이다. 현재 사용법은 [명령 목록](../reference/tooling.md)을 따른다. 과거 결과의 실행 명령·소스 해시는 당시 값을 유지한다. 아래 별칭은 호환을 위해 계속 지원한다.

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


`scripts/edge_asr.py`는 현재 체크아웃 진입점, `benchmark_zipformer.py`는 M1 호환 진입점, `deploy_rp5.sh`는 과거 rsync 재현용이다. 평소 배포는 main push/pull을 사용한다. 이전 Python 내부 모듈 경로 자체가 필요하면 해당 Git 커밋을 별도 체크아웃한다.
