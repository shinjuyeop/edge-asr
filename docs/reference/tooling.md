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

추가 진단 명령 `edge-asr tools inspect-cooling`은 `reports.cooling`에서 기존 `--run` 기록 재분석 또는 `--live` 순간 센서 조회만 수행한다. `--output`에는 새 JSON 파일을 지정한다. 부하 실행과 OS 설정 변경은 없다. `probe-annotated-vad --backend bounded`는 별도 Python ORT를 사용하는 실험 경로이며 기본값은 기존 `native`다. [구현 범위와 실제 검증 절차](../research/m3_bounded_vad.md)를 따른다.

`edge-asr tools probe-vad-duration`은 파생 WAV의 30초 초과 활성·3/5/8초 소유 PCM·RSS·reset 계측을 위한 별도 공학 검사다. 실제 정확도나 마이크 지연 평가를 대신하지 않는다. `probes.vad_duration`에 구현하며 동일한 외부 열/실행 제한 안에서 사용한다.

`edge-asr tools prepare-vad-overlap --output <새 data 경로>`는 기존 개발 WAV 목록에서 1/2/3트랙의 고정 위상 평균 입력과 `vad-duration-input-v1`을 만든다. 원본 해시와 개발 split을 확인하고 기존 출력은 거부한다. 중첩 입력에는 확정 전사를 붙이지 않는다.

`thermal-run --temperature-policy warn75-stop80`은 승인된 별도 75°C 경고/80°C 중단 정책이다. 기본 `legacy75`는 과거와 같은 75°C 중단이다. [정책·실행 범위](../research/m3_thermal_policy.md)와 새 영수증의 정책 ID를 확인한다.

`thermal-run --temperature-policy operational`은 기본 열 보호를 유지한 운영 부하 검사다. 온도/감속을 기록하며 계속 실행하고 저전압/계측 누락/외부 제한 실패를 구분한다. `operational_valid`와 `control_valid=false`를 함께 기록하므로 기존 통제 비교 집계에 합치지 않는다. `probe-load`의 호출별 monotonic 시각으로 열·TID 표본을 연결할 수 있다.

`probe-segmented-accuracy`와 `summarize-segmented-accuracy`는 [고정한 세 구간 정책의 파일 CER 비교](../research/m3_segmentation_accuracy.md)와 모델 없는 재채점이다. 일반 batch 평가의 순수 ASR RTF와 구간 도구의 파이프라인 RTF를 구분한다.

추가 조건 `bounded_vad_5s_context200ms`는 [최대 200ms 실제 왼쪽 문맥](../research/m3_vad_context.md)을 포함한다. 겹치지 않는 소유 프레임 합과 문맥 포함 payload 합을 구분하며, 중복 전사를 제거하지 않는다.

`probe-concurrent`와 `summarize-concurrent`는 [M3-1 동시 부하](../research/m31_concurrent.md) 측정/재집계다. 단독 1/2T와 동시 1+1/1+2/2+1을 같은 원음에서 비교한다. Refine 구간 끝 기준 지연과 실제 Hybrid 병합 지연을 구분한다.

`replay-merge --run <완료된 동시 기록> --policy all --output <새 폴더>`는 [저장 실측의 파일 ID 단위 선택 정책](../research/m32_merge_replay.md)을 모델 없이 재생한다. `reports.merge_replay`에 구현하며 원시 체크섬/재집계를 검증한다. 정답은 선택 뒤 채점에만 쓰고, child 준비 시각에서 유도한 선택 대기를 실제 UI 지연으로 표시하지 않는다.

`probe-concurrent --refine-policy vad0 --segment-merge`는 [같은 VAD 구간의 실제 병합 실험](../research/m33_segment_merge.md)이다. 구간별 Zip 상태 reset·300ms 보류가 있는 별도 조건으로, 기존 연속 Zip 모드는 유지한다. 실제 parent 수신/상태 적용과 UI 렌더링을 구분한다. `summarize-segment-merge`는 원시 모델 출력·PCM·이벤트/최종 상태를 모델 없이 교차 검증한다.
