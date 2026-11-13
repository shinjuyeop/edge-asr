# 코드 구조

현재 작업은 `qwen/`의 Python 실행 경로와 `native/`의 C++ 연결에 모읍니다.

| 위치 | 역할 |
| --- | --- |
| `scripts/edge_asr.py` → `cli.py`, `tools/cli.py` | 공개 명령·선택 구현의 지연 import |
| `qwen/onnx.py`, `qwen/gguf.py` | 모델 로딩·발화 상태·전사·출력 검사 |
| `qwen/benchmark.py` | 모델1회 로딩·첫 추론·워밍업·반복·고정 PCM 재현 |
| `qwen/profiling.py` | ONNX Python/ORT 호출 경계·세션/시계 검증 |
| `qwen/realtime/` | 1배속 실행·VAD 접두 부분 전사·유한 단일 작업자. `stability.py`로 접두부 재표시·문자 철회 집계. 공통 입력·구간 분할 재사용 |
| `native/gguf/`, `native/ort_trace/` | C ABI·ORT 계측과 빌드. [안내](../../native/README.md) |
| `tools/data/` | 데이터 준비·검증·평가 순서 생성 |
| `dataset.py`, `config.py`, `audio_policy.py` | manifest·PCM·해시·설정 검사 |
| `evaluation.py`, `scoring.py`, `metrics.py` | 일반 평가·실패 보존·CER/RTF·재채점 |
| `artifacts.py`, `resources.py`, `runtime.py`, `diagnostics.py` | 원자적 기록·공통 계측·native 경고·런타임 진단 |
| `tools/probes/`, `thermal.py`, `cooling.py` | 열·시간·메모리 제한과 냉각 점검 |
| `streaming.py`, `segmentation.py`, `vad*.py`, `textgrid.py` | 재사용 입력·큐·구간/VAD·주석 기반 |
| `microphone.py` | 모델 독립 ALSA 장치 조회·16kHz 수집·유한 큐·녹음/실패 보존 |
| `adapters/`, `legacy/` | 공통 factory·이전 비교 모델과 자막/병합 재현 |
| `legacy/benchmark_zipformer.py` | M1 Zipformer 전용 반복 프로브. 현재 Qwen의 `qwen/benchmark.py`와 구분 |

```mermaid
flowchart LR
  CLI[CLI] --> Q[Qwen 실행기]
  Q --> A[ONNX / GGUF]
  A --> N[native / 외부 런타임]
  Q --> C[공통 입력·채점·계측]
  CLI --> L[legacy 재현]
  L --> C
```

어댑터 계약은 `model_files`, `load()`, `transcribe(Audio) -> str`, `close()`입니다. 공통 factory는 선택 모델만 import합니다. GGUF는 ctypes로 별도 C ABI에 같은 float PCM을 전달합니다. `realtime/`은 도착한 접두 PCM을 반복 호출해 부분 자막을 갱신합니다. 새 GGUF bridge의 선택적 `transcribe_stream(Audio, on_text)`는 생성 중 텍스트를 임시 배포하며 기존 계약·최종 채점은 유지합니다. 음성 청크 간 KV 재사용·encoder 증분 실행은 아직 없습니다. [측정 범위](../research/qwen_realtime.md).

GGUF의 선택 설정 `num_threads_batch`는 여러 토큰 prefill과 오디오 인코딩에 적용하고 `num_threads`는 토큰 생성에 적용합니다. 생략하면 기존처럼 같은 값을 쓰며 기존5인자 생성 ABI를 사용합니다. 명시할 때는 `split_threads_v1` 별도 빌드의6인자 생성 함수를 사용하고 반환된 설정을 검사합니다. `audio_threads`는 mtmd 초기화에 전달한 값의 기록이며 별도 runtime getter가 아닙니다.

선택 `cache_text_prefix`는 음성 앞 동일 text KV만 유지하는 별도 GGUF 실험 기능입니다. 기본은 끔이며 명시할 때 `text_prefix_cache_v1` 빌드를 요구합니다. `native/gguf/prefix_cache.h`가 일치/실패 상태를 관리하고 bridge는 그 뒤 KV를 제거합니다. audio/뒤 text/이전 생성 결과는 재계산하며 실패 시 전체 초기화로 돌아갑니다. 앞/뒤 text·audio encoder·embedding 평가 시간을 분리하고 첫 추론/워밍업도 기록합니다. 기존 빌드는 보존하며 현재 공유 개발 설정은 검증한 고정 prefix KV 켬을 사용합니다.

토큰 표시의 `hold_replayed_prefix_v1`은 같은 구간의 다른 호출이 이미 보인 원문 접두부를 다시 생성할 때 이전 문자열을 유지합니다. 확장·실제 내용 변경·종료 result는 전달하며 실패 시 임시 문자를 확정하지 않습니다. 원시 토큰은 숨기지 않고 `prefix_replay_suppressed`로 기록합니다. `caption_stability`의 문자 철회량과 자기 최종 전사 일치는 정답 CER·음향 지연과 구분합니다.

가변 후속의 기본 비용 추정은 `total`입니다. 선택 `partial_cost_model=audio`는 같은 작업자의 최근3개 성공 native 계측에서 음성 처리 부분만 입력 길이에 따라 확대합니다. 단계 계측이 없거나 실패하면 기존 추정으로 돌아가며, 첫 요청 보호·유휴 조건·구간당 비용/횟수 예산·최종 우선순위는 유지합니다. [선별 조건](../research/qwen_realtime.md#음성-단계-비용-추정-선별--2026-11-13-사전-조건).

공통 코드와 Qwen은 `legacy`를 import하지 않습니다. CLI dispatch는 예외이며 보고서는 실행 probe에 의존하지 않습니다. `write_json`은 원자적 체크포인트, JSONL 이벤트는 행별 보존입니다. 입력·출력/실패·`ko_cer_v1` 계약을 유지하며 Host 모의 검사와 RP5 실제 추론을 구분합니다. [테스트](../../tests/README.md), [경로 이행](structure_migration.md).

새 개발은 [공유 GGUF 설정](../../configs/gguf_realtime.toml)과 [개발 안내](../../CONTRIBUTING.md)에서 시작합니다. CLI 목록은 현재·이전 Qwen 진단·legacy·유지보수로 구분하지만 기존 명령은 같은 실행기로 전달합니다. 환경·외부 바이너리는 [환경 기록](environment.md)에, 모델 없는 자동 검사는 `.github/workflows/host.yml`에 있습니다.

## 청취 도구

`qwen/viewer/`는 기존 연속 실행 기록을 읽어 모델 조건과 독립적으로 라이브·기록 화면을 제공합니다. `runs.py`는 등록한 manifest의 해시로 원음을 재구성하고 기존 replay/thermal-run을 실행합니다. `analysis.py`는 원음별 지연 정의를 공유하며 웹 서버·추론에 의존하지 않습니다. `web/`은 음원 재생과 표시만 담당합니다. 선택 시작 gate는 입력 생산자 준비 뒤 열리며, 기존 평가에서는 사용하지 않습니다. 서버는 localhost와 SSH 터널로 접근합니다. [사용법·계측 범위](../guides/listener.md).

마이크도 같은 `replay.run`·`CaptionSession`·모델 어댑터·채점을 사용합니다. `microphone.py`가 별도 arecord 프로세스와 수집 스레드·유한 큐로 PCM을 공급하며, 길이 미정 세션은 실제 도착 프레임에서 정상 종료합니다. `worker.input.kind = microphone`으로 파일 입력과 구분합니다. 웹의 입력 종료 파일은 수집만 멈추고 Native 최종 배출을 기다립니다. 녹음 재생은 등록 manifest 대신 저장된 WAV 해시/프레임을 확인하고, 지연은 완료 VAD/ASR 구간으로 집계합니다.
