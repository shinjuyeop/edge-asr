# 코드 구조와 확장 경계

공통 ASR 실행과 모델 호출을 분리하고, 연구 도구가 공통 패키지에 의존하도록 구성합니다. 모델별 분기를 공통 평가 루프에 추가하지 않습니다.

| 영역 | 책임 |
| --- | --- |
| `cli.py` | 설정·환경·평가·재채점·도구 명령 진입점 |
| `adapters/` | 모델 로딩·발화별 상태·전사·모델 제어 태그 |
| `dataset.py`, `metrics.py` | manifest/PCM16·공통 문자 정규화/CER·분위수 |
| `evaluation.py`, `scoring.py` | 모델 1회 로딩·실행·실패 보존·추론 없는 재채점 |
| `streaming.py` | 독립 입력 프로세스·유한 큐·1배속 시계·이벤트 |
| `audio_policy.py`, `segmentation.py` | 100ms 전처리 상태·공통 PCM·발화 구간 소유권/문맥 |
| `vad.py`, `vad_probe.py`, `vad_annotations.py` | 검증한 VAD 설정·구간 진단·주석 검증/채점 |
| `vad_probability.py`, `cooling.py` | 선택적 확률 VAD 실험·읽기 전용 냉각 센서/과거 기록 진단 |
| `artifacts.py`, `resources.py` | 해시/JSON 산출물·CPU/RSS 표본 |
| `runtime.py`, `diagnostics.py`, `thermal.py` | 네이티브/Python 런타임 구분·TID CPU·온도/제한 상태 |
| `tools/data/` | 원본 보존·자료 파생·출처/해시·검수 결정 반영 |
| `tools/probes/` | RP5 비교·부하/VAD 실측·외부 실행 제한 |
| `tools/reports/` | 원시 대조·재채점·집계. 모델 실행 없음 |
| `tools/maintenance/` | 이전 clone/벤치마크 폴더의 보존 통합 |

## 의존 방향과 실행

`tools → 공통 계약 → 표준 라이브러리` 방향입니다. 보고서 코드가 실행 도구에서 설정 상수를 가져오지 않으며, 공통 패키지는 도구를 import하지 않습니다. CLI는 선택된 명령의 모듈만 지연 import합니다. 추상 기반 클래스·외부 플러그인 검색·새 프로세스 프레임워크는 도입하지 않습니다.

모델 어댑터의 공통 계약은 `model_files`, `load()`, `transcribe(Audio) -> str`, `close()`입니다. 선택 메타데이터로 설정·raw 결과·출력 정리 정책을 남깁니다. Zipformer는 추가로 `start_stream`, `accept_chunk`, `finish_stream`을 제공하고 SenseVoice는 batch만 지원합니다. 지원하지 않는 모드는 실행 전에 거부합니다. 새 모델은 구체 어댑터·설정 검증/지원 모드·factory 분기·계약 테스트를 추가합니다.

데이터 준비·결과 집계는 NumPy/sherpa를 import하지 않습니다. 실제 모델이 필요한 함수에서 선택 런타임을 import합니다. 환경의 명시적 `--probe-runtime`은 모델 생성 없이 네이티브 버전을 조회합니다.

`artifacts.write_json`은 같은 디렉터리의 임시 파일로 체크포인트를 원자적으로 교체하며 비유한 JSON을 거부합니다. 호출자는 새 실행 경로를 먼저 확보합니다. `resources.RssSampler`는 현재 프로세스의 관측 RSS이며 정확한 순간 피크를 보장하지 않습니다. CPU/RSS와 모델 로딩/추론 구간의 정의는 [지표 문서](benchmark_design.md)를 따릅니다.

## 재현성과 호환성

기존 JSONL·모델 설정·`ko_cer_v1`·RTF 정의·실험 원본은 유지합니다. 과거 실행 영수증의 명령과 소스 해시는 당시 값입니다. 현재 명령은 [도구 이행표](tooling.md)를 사용합니다. 이전 import 경로의 `benchmark.digest/write_json/distribution/process_sample`, `evaluation.RssSampler`, `segmentation.pcm_bytes`는 같은 공통 함수를 제공하는 호환 경로로 남깁니다.

현재 모델 설정은 `configs/`에, 원본 프로브에 자동 적용되지 않는 M1 탐색 설정은 `configs/legacy/`에 둡니다. 설치된 패키지는 `tools/`를 포함하며 기존 RP5에서는 `scripts/edge_asr.py`가 체크아웃의 `src`를 사용하므로 재설치가 필요 없습니다.

구조 정리의 테스트는 모델을 쓰지 않는 계약/회귀 검사와 과거 결과의 재채점입니다. 실제 ARM64 모델 실행 검증은 각 RP5 기록에서 따로 확인합니다. 현재 남은 제한과 다음 과제는 [현재 상태](../status.md)를 따릅니다.
