# OPT-1 기존 ONNX 경로의 호출 경계 계측 — 2026-11-09

현재 sherpa-onnx 2.0.1/native ORT 1.28.2·동일 INT8 자산·2T를 유지하며 일반 발화와 56ms 반복 사례의 비용을 나눕니다. [OPT-0 기준선](../../results/summaries/optimization/opt0-pilot-20261109.md)을 출발점으로 사용합니다. 신규 실행 ID는 `opt1-profile-20261109-01`입니다.

## 계측 경로

RP5의 기존 `_sherpa_onnx`는 ORT 1.28.2 공유 라이브러리의 `OrtGetApiBase`를 동적으로 참조합니다. 별도 LD_PRELOAD 라이브러리에서 C API table의 `CreateSession`, `ReleaseSession`, `Run`만 감싸고 나머지 포인터와 입력·출력·옵션은 원본에 전달합니다. 기존 wheel·모델을 수정하거나 새 inference 엔진을 만들지 않습니다. 선택된 CPU 파일 경로의 세션 이름과 순서가 맞지 않으면 계측을 실패 처리합니다.

공식 [ORT 1.28.2 C API](https://github.com/microsoft/onnxruntime/blob/v1.28.2/include/onnxruntime/core/session/onnxruntime_c_api.h)의 ABI 28을 사용하며, 헤더 SHA-256과 실제 runtime 버전을 검사합니다. 공식 [profiling 안내](https://onnxruntime.ai/docs/performance/tune-performance/profiling-tools.html)의 노드별 ORT 내장 profiler와는 다른, API 호출 경계의 가벼운 계측입니다. 원본 [sherpa CPU 모델 구현](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/offline-qwen3-asr-model.cc)에서 Conv·Encoder와 매 LLM 단계가 해당 세션의 Run을 호출하는 것을 확인했습니다.

계측 코드는 [ort_trace.cc](../../native/ort_trace/ort_trace.cc), Python 경계/검증은 [profiling.py](../../src/edge_asr/qwen/profiling.py)에 있습니다. 모델 로딩은 기존 어댑터를 사용하고 stream/recognizer proxy는 동일 인자를 전달하며 경계만 기록합니다. native warning과 실패 정책도 기존 어댑터가 담당합니다.

| 기록 | 경계·해석 |
| --- | --- |
| create_stream | Python의 새 OfflineStream 생성 호출 |
| accept_waveform | Python 입력 전달·native 특징 추출 호출 전체 |
| decode_stream | 기존 C++ 디코딩 호출 전체 |
| ort_conv_s / ort_encoder_s | 대응하는 CPU ORT Run wall time. C++ 단계 전체와 구분 |
| ort_prefill_s | 한 전사의 첫 decoder Run |
| ort_token_decode_s / calls | 이후 decoder Run 시간 합·호출 수. 반환 text 토큰 수와 구분 |
| native_non_ort_s | decode_stream - ORT Run 합. KV 갱신·토큰 선택·토크나이저·준비 등이 섞이며 특정 연산으로 단정하지 않음 |
| decoder_inter_run_gap_s | prefill 이후 연속 decoder Run 사이 시간 합. KV 갱신만의 시간이 아님 |
| first_logits_ready_s | 준비 PCM의 transcribe 진입→첫 prefill Run 종료 |
| first_decode_entry_s | 같은 시작→다음 decoder Run 진입. 이때 첫 비EOS 모델 토큰은 이미 선택됨 |
| ttft_s | 실제 토큰 선택 완료/호출자 반환 callback이 없어 null |

`first_logits_ready_s`~`first_decode_entry_s`는 다음 decode가 존재할 때 내부 첫 토큰 선택 시점의 경계를 제공합니다. 외부 자막의 첫 문자나 실제 TTFT로 이름을 바꾸지 않습니다. CPU CLOCK_MONOTONIC과 Python perf_counter가 같은 시계인지 확인하고, native event가 decode_stream 내부의 순차 범위에 드는지 검사합니다.

Run 내부에는 디스크 I/O가 없고 시간·종류·오류 상태만 256개 고정 버퍼에 기록합니다. 버퍼 초과·미확인 세션·순서/시계 불일치는 무효입니다. 원본 Run 오류는 그대로 전달하며 실패 시에도 profile과 native stderr를 보존합니다. 계측 비활성 시간/호출 수는 0으로 채우지 않고 null입니다.

## 입력·실행 순서

OPT-0의 동일 KSS 6개 manifest와 해시 검증된 재현 8개 manifest를 읽습니다. 기존 입력/원시는 수정하지 않습니다.

1. Stock anchor: 기존 Qwen adapter로 6개 각 1회, 첫 추론 1회·워밍업 3회. preload 없음.
2. Profile ABBA: 동일 모델 프로세스에서 6개×4라운드. 첫 추론·워밍업 계측 off, scored 라운드 1/4는 off, 2/3는 on. 같은 proxy와 API 전달 경로에서 native 시계/기록의 영향을 비교합니다. 파일 시작은 기존 회전 순서입니다.
3. Diagnostic profile: 별도 모델 프로세스에서 기존 고정 진단 8개 순차 실행, 전부 on, 워밍업 없음. 56ms 반복 경고 3개를 실패로 보존합니다.

기존 가설과 정규화 전사·실패 유형을 비교합니다. ABBA 24/24 정상 파일 성공·문자 커버리지 100%, 동일 전사를 회귀 기준으로 둡니다. 진단은 API 성공/실패와 계측 성공을 분리하고 구간 정답 부재로 CER=null입니다.

ABBA on/off의 파일별 처리 중앙값 차이와 wrapper 추가 시간(외부 호출 시간 - 내부 transcribe 시간)을 보고합니다. on/off 차이가 5%를 넘으면 추가 오버헤드/실행 상태 검토 대상으로 표시합니다. 운영 온도·클럭 변동과 작은 n 때문에 이 차이를 순수 계측 오버헤드의 정확한 추정으로 부르지 않습니다. stock anchor도 단일 탐색이며 속도 개선 증거가 아닙니다.

사용자 요청에 따라 `operational --skip-temperature-admission`으로 온도 대기·중단 없이 순차 실행합니다. 기본 펌웨어 보호·4GiB·외부 300초·전압/필수 센서 검사와 온도·클럭·팬/제한 기록은 유지합니다. 기존 환경·패키지·모델·과거 결과는 보존합니다.

## 별도 빌드와 재현

[빌드 안내](../../native/README.md)에 따라 공식 헤더는 `runtimes/qwen3-opt/ort-trace-20261109-01/include/`, 라이브러리/영수증은 장비별 새 디렉터리에 둡니다. 원본 ORT 라이브러리 경로는 `EDGE_ASR_ORT_LIBRARY`, 새 계측 라이브러리는 해당 자식 프로세스의 `LD_PRELOAD`로만 지정합니다. 기존 환경을 재설치하지 않습니다.

Host/RP5의 native mock 검사는 실제 ASR와 구분합니다. fake C API에 대한 전달·출력·오류 보존, 전사별 reset, 비활성 동작, overflow, 세션 해제를 검증한 다음 RP5 실제 hook과 추론을 확인합니다. 원시·event는 `results/optimization/opt1/opt1-profile-20261109-01/`, 테스트·보존·분석은 대응 maintenance 아래에 기록합니다.

2026-11-10 경로 정리: 위 당시 실행 명령·조건은 기록으로 유지합니다. 현재 소스·설정·준비 CLI는 [구조 이행표](../reference/structure_migration.md)와 [실행 안내](../guides/qwen3_optimization.md)를 따릅니다.
