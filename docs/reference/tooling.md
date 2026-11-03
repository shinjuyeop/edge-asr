# 실행 명령

명령은 선택한 도구만 지연 import하며 모델을 자동 다운로드하지 않는다. 설치된 `edge-asr`, `python -m edge_asr`, 체크아웃의 `scripts/edge_asr.py`는 같은 진입점이다. 기존 RP5는 재설치 없이 체크아웃 진입점을 사용한다.

```bash
.venv/bin/python scripts/edge_asr.py --help
.venv/bin/python scripts/edge_asr.py live --help
.venv/bin/python scripts/edge_asr.py tools list
.venv/bin/python scripts/edge_asr.py tools probe-concurrent --help
```

## 평소 사용하는 명령

| 명령 | 목적 | 안내 |
| --- | --- | --- |
| `live --wav ...` / `live --manifest ...` | Zip 자막·보정 후보 분리 표시 | [WAV MVP](../guides/live_captions.md) |
| `evaluate --config ... --manifest ...` | batch/paced_stream 실제 평가 | [평가](../guides/evaluation.md), [1배속](../guides/streaming_evaluation.md) |
| `score --predictions ...` | 모델 없는 재채점 | [평가·재채점](../guides/evaluation.md#재채점) |
| `env [--probe-runtime]` | 현재 장비·패키지·선택적 native ORT 조회 | [설치](../guides/setup.md) |
| `check-config --evaluation ...` | TOML/모드 검증 | [설정](../../configs/README.md) |
| `threads --pid ...` | 실행 중인 PID의 TID별 CPU 관찰 | [SenseVoice/CPU 진단](../research/m2_sensevoice.md) |

## 연구·자료 도구

| `tools` 명령 그룹 | 역할 |
| --- | --- |
| `prepare-official`, `prepare-kss`, `prepare-external` | 출처/해시를 고정한 평가 데이터 준비 |
| `prepare-volume`, `prepare-vad`, `prepare-vad-overlap`, `prepare-segments` | 원본 보존 파생 입력·구간 공학 검사 |
| `prepare-public-review`, `materialize-review`, `prepare-annotations`, `review-annotations` | 공개 음성·검수/불확실 주석 관리 |
| `compare`, `probe-*` | RP5 모델/전처리/VAD/구간/동시 부하 측정 |
| `thermal-run` | 외부 시간/메모리/열 감시. [통제/운영 정책](../research/m3_thermal_policy.md) |
| `summarize-*`, `replay-merge`, `replay-splice` | 저장 원시 대조·모델 없는 집계/정책 재생 |
| `inspect-cooling` | 센서/기존 열 기록의 읽기 전용 점검 |
| `repeat-zipformer` | M1 단일 샘플 반복 프로브 |
| `consolidate` | 과거 다중 RP5 폴더의 일회성 보존 통합 |

구체 옵션과 전체 목록은 CLI help를 기준으로 한다. 실험별 입력·조건·제약은 [연구 색인](../research/README.md), 실제 수치는 [결과 색인](../../results/summaries/README.md)에 둔다. `probe-concurrent`의 구간 reset 병합·연속 token 진단·자막 표시는 서로 다른 모드이며 동시에 활성화하지 않는다.

옛 `scripts/*.py` 명령은 [과거 명령 이행표](../archive/command_migration.md)에 모았다. 별칭은 유지하지만 새 문서는 현재 명령을 사용한다. 과거 원시/영수증의 명령이나 소스 해시를 현재 경로로 소급 변경하지 않는다.
