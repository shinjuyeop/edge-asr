# 실행 명령

명령은 선택한 도구만 지연 import하며 모델을 자동 다운로드하지 않는다. 설치된 `edge-asr`, `python -m edge_asr`, 체크아웃의 `scripts/edge_asr.py`는 같은 진입점이다. 기존 RP5는 재설치 없이 체크아웃 진입점을 사용한다.

```bash
.venv/bin/python scripts/edge_asr.py --help
.venv/bin/python scripts/edge_asr.py live --help
.venv/bin/python scripts/edge_asr.py tools list
.venv/bin/python scripts/edge_asr.py tools probe-concurrent --help
```

## 공통 명령

| 명령 | 목적 | 안내 |
| --- | --- | --- |
| `live --wav ...` / `live --manifest ...` | Zip 자막·보정 후보 분리 표시 | [WAV MVP](../guides/legacy/live_captions.md) |
| `evaluate --config ... --manifest ...` | batch/paced_stream 실제 평가 | [평가](../guides/evaluation.md), [1배속](../guides/legacy/streaming_evaluation.md) |
| `score --predictions ...` | 모델 없는 재채점 | [평가·재채점](../guides/evaluation.md#재채점) |
| `env [--probe-runtime]` | 현재 장비·패키지·선택적 native ORT 조회 | [설치](../guides/setup.md) |
| `check-config --evaluation ...` | TOML/모드 검증 | [설정](../../configs/README.md) |
| `threads --pid ...` | 실행 중인 PID의 TID별 CPU 관찰 | [SenseVoice/CPU 진단](../archive/model-comparison/m2_sensevoice.md) |

## 도구의 현재 소속

새 Qwen 작업은 `tools/optimization/baseline.py`의 `benchmark-qwen3`로 첫 추론·워밍업·기준선을 측정합니다. `thermal-run`, `probe-load`, 데이터 준비·냉각 검사·유지보수는 공통 도구입니다. `live`, 모델 비교·VAD·병합 실행/보고서는 `legacy/tools/`에 있으며 기존 명령 이름은 유지합니다. `tools list`에서 실제 모듈 위치를 확인할 수 있습니다. [새 OPT 실행 안내](../guides/qwen3_optimization.md).

## 기존 연구·자료 명령

| `tools` 명령 그룹 | 역할 |
| --- | --- |
| `prepare-official`, `prepare-kss`, `prepare-external` | 출처/해시를 고정한 평가 데이터 준비 |
| `prepare-volume`, `prepare-vad`, `prepare-vad-overlap`, `prepare-segments` | 원본 보존 파생 입력·구간 공학 검사 |
| `prepare-public-review`, `materialize-review`, `prepare-annotations`, `review-annotations` | 공개 음성·검수/불확실 주석 관리 |
| `compare`, `probe-*` | RP5 모델/전처리/VAD/구간/동시 부하 측정 |
| `profile-qwen3` | [기존 ORT의 호출 경계 계측](../research/opt1_profiling.md), 별도 preload·on/off/abba |
| `opt0-baseline`, `benchmark-qwen3` | [Qwen 단독 반복·고정 PCM 재현](../research/opt0_pilot.md), 첫 추론/워밍업/반복 구분. [OPT-2](../research/opt2_gguf.md)의 ONNX/GGUF도 같은 실행기 사용 |
| `compare-qwen3` | [Qwen 별도 환경의 첫 실행·세 모델 KSS 단회 탐색](../archive/model-comparison/qwen3_batch.md) |
| `compare-qwen3-realtime` | [Qwen VAD 구간·Zip 동시 실행](../archive/model-comparison/qwen3_realtime.md), 사전 고정 순서·실패 보존 |
| `prepare-validation`, `validate-files` | [새 문장 30개와 30분 연속 입력](../archive/model-comparison/file_validation.md), 별도 워밍업·원본 보존·고정 순서 |
| `thermal-run` | 외부 시간/메모리/열 감시. [통제/운영 정책](../archive/model-comparison/m3_thermal_policy.md) |
| `summarize-*`, `replay-merge`, `replay-splice` | 저장 원시 대조·모델 없는 집계/정책 재생 |
| `inspect-cooling` | 센서/기존 열 기록의 읽기 전용 점검 |
| `repeat-zipformer` | M1 단일 샘플 반복 프로브 |
| `consolidate` | 과거 다중 RP5 폴더의 일회성 보존 통합 |

구체 옵션과 전체 목록은 CLI help를 기준으로 한다. 실험별 입력·조건·제약은 [연구 색인](../archive/model-comparison/README.md), 실제 수치는 [결과 색인](../../results/summaries/README.md)에 둔다. `probe-concurrent`의 구간 reset 병합·연속 token 진단·자막 표시는 서로 다른 모드이며 동시에 활성화하지 않는다.

옛 `scripts/*.py` 명령은 [과거 명령 이행표](../archive/command_migration.md)에 모았다. 별칭은 유지하지만 새 문서는 현재 명령을 사용한다. 과거 원시/영수증의 명령이나 소스 해시를 현재 경로로 소급 변경하지 않는다.

사용자가 온도 대기 없이 운영 검사를 선택한 경우 `thermal-run --temperature-policy operational --skip-temperature-admission`을 명시합니다. 시작 온도는 관측만 하고 온도/열 감속으로 중단하지 않으며, 기본 펌웨어 보호·전압/필수 센서·외부 시간·메모리 제한은 적용합니다. 기존 기본 정책은 유지합니다. [실험 조건 변경 기록](../research/opt0_pilot.md#프로토콜-수정-v3--사용자-지정-운영-조건).

새100개 비교에서는 명시적 `warn75-stop80 --timeout-s 1800`도 지원합니다. 기존 시작53°C·75°C 경고·80°C/현재 제한/결측 중단은 유지하고 실제 ARM 클럭을 monitor/after에 기록합니다. legacy 기본600초와 운영 최대2100초는 그대로입니다. [실행 순서·열 조건 실패 시 별도 운영 측정](../research/fixed100_baseline.md)을 따릅니다.

초기 배포용 `probe-qwen3`는 중복 native 호출을 제거하며 폐기했습니다. 현재 첫 추론 검사는 `benchmark-qwen3`를 사용합니다. 옛 smoke의 인자·debug/출력 규격을 그대로 재현하려면 정리 전 commit `f88b33f`를 사용합니다. 과거 기록은 변경하지 않습니다.
