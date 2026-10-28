# 초기 구조와 확장 방침

## 현재 구현

`src/edge_asr`는 `env`, `check-config`, `evaluate`, `score`, 선택 진단 `threads` CLI와 별도 프로브 반복 모듈을 제공합니다. `dataset.py`는 JSONL·WAV, `metrics.py`는 정규화·CER, `evaluation.py`는 상주 모델 실행·계측, `streaming.py`는 독립 입력·유한 큐·paced 이벤트, `scoring.py`는 집계·저장·재채점을 담당합니다. `runtime.py`는 sherpa 네이티브 ORT와 Python ORT 버전을 구분하고 `diagnostics.py`는 기존 Linux PID의 TID별 CPU를 읽습니다. 추론 의존성은 어댑터의 load 시점에 import하며 기본 env·설정 검사·score에는 필요하지 않습니다. 명시적 `env --probe-runtime`만 모델 생성 없이 sherpa를 import합니다.

`scripts/edge_asr.py`로 기존 RP5 환경을 재설치하지 않고 현재 소스를 사용할 수 있습니다. 첫 공통 구현은 CPU Zipformer의 batch 평가입니다. 최초 유효 WAV로 모델 전체 워밍업을 수행한 뒤 파일마다 새 상태로 1회 인식하며 정답·가설·실패를 저장합니다. 모델·데이터·정규화·소스·환경을 함께 기록하고 결과를 덮어쓰지 않습니다. 재채점은 저장한 가설·시간만 읽어 새 보고서를 만듭니다. 실제 RP5에서 공식 샘플 1개와 KSS 개발용 50개 발화의 동작을 확인했으며 [평가 안내](evaluation.md)의 계측 범위를 따릅니다.

`scripts/prepare_kss_subset.py`는 Host에서만 새 데이터를 다운로드·변환하고, 고정 데이터 리비전·행·전사·원본/변환 해시를 보관합니다. RP5로 같은 준비 폴더를 전달한 뒤에는 해시를 확인하여 재사용합니다. 데이터 준비는 모델 어댑터와 분리하며 일반 평가기는 기존 JSONL·WAV 계약을 그대로 사용합니다.

`scripts/benchmark_zipformer.py`는 설치 없이 `src/edge_asr/benchmark.py`를 호출합니다. 반복 도구는 지정한 기존 가상환경의 자식 프로세스에서 런타임 import를 검사하고 원본 프로브를 실행합니다. WAV 포맷·자산 체크섬, 워밍업/반복·순서, 실패·시간 초과·중단, 로그 파싱·분위수, 전체 프로세스 CPU와 표본 RSS를 담당합니다. 추론 라이브러리를 부모에서 import하거나 원본을 수정하지 않습니다. 자산 경로와 프로브의 100ms 청크·flush 설정은 원본에 고정되어 있습니다. 자세한 측정 범위는 [반복 안내](rp5_repeat_benchmark.md)를 따릅니다.

`configs/zipformer_paced.toml`은 새 공통 1배속 평가 설정입니다. 입력 프로세스는 절대 오디오 시각에 청크를 공개하고 추론과 독립적으로 진행합니다. 유한 큐의 drop_newest·실제 손실·대기 백로그를 기록하며 batch와 같은 입력/CER·모델 1회 로딩·실패 저장·재채점을 사용합니다. 구체적인 계측은 [스트리밍 안내](streaming_evaluation.md)를 따릅니다.

`configs/zipformer_streaming.toml`은 기존 탐색 설정입니다. 보존된 `experiments/m1_zipformer/streaming_probe.py`는 환경 변수 `ASR_THREADS`와 코드 내 고정 경로를 사용하며 이 TOML을 읽지 않습니다. 그 파일의 `warmup_runs=3`, `repetitions=30`도 자동 실행되지 않습니다. 새 공통 평가에는 위 `zipformer_paced.toml`을 사용합니다.

`experiments/`는 탐색과 출처 보존 영역입니다. 기존 원본을 공통 패키지에서 import하면 파일 입출력·추론이 즉시 실행되므로 import하지 않습니다. 반복 도구도 이를 별도 프로세스로만 실행합니다. 새로운 실험은 별도 파일 또는 하위 디렉터리에 추가합니다.

## 추후 어댑터 경계

현재 구체 모듈 `adapters/zipformer.py`, `adapters/sensevoice.py`와 `create_adapter` 분기를 사용합니다. 경계는 `model_files`, `load()`, `transcribe(Audio) -> str`, `close()`입니다. 평가 실행기는 모델을 한 번 로드하고 모델별 호출만 위임합니다. 선택 속성 `metadata`와 `last_result_metadata`로 설정·출력 정책과 raw 결과를 저장하며 공통 추론 루프에 모델별 분기를 넣지 않습니다. 지원 모드 표와 모델별 설정 검증에서 SenseVoice의 paced_stream과 미구현 Qwen을 실행 전에 거부합니다.

SenseVoice는 `OfflineRecognizer.from_sense_voice()`와 발화별 새 Offline 상태를 사용합니다. 언어·ITN을 명시하고 제어 태그를 어댑터 내부에서 분리합니다. 특징 규격은 해당 factory의 규격이며 Zipformer의 padding·온라인 호출을 사용하지 않습니다. 공통 입력·CER·RTF·재채점은 재사용합니다. [M2 안내](m2_sensevoice.md)의 계측·출력 계약을 따르며 Host mock 후 RP5에서 단일 WAV·같은 KSS 50개 batch 실행과 결과 회수·재채점을 확인했습니다.

Zipformer의 paced 모드에는 구체적인 `start_stream()`, `accept_chunk(stream, samples) -> str`, `finish_stream(stream) -> str`를 추가했습니다. 입력/큐·시계·로그는 공통 실행기가 소유하고 어댑터는 모델 호출만 담당합니다. batch의 기존 호출 순서는 유지했으며 스트리밍을 지원하지 않는 모델에 이 능력을 가정하지 않습니다. 새로운 추상 기반 클래스·플러그인 계층은 추가하지 않았습니다.

어댑터가 담당할 것은 모델 로딩, PCM 입력 전달, 부분/최종 결과 수집과 모델 제어 태그 분리입니다. 파일 입력, 정답 정규화, 시간 계측, CPU/RSS 수집, 결과 저장은 공통 실행기에서 담당합니다. 로딩 자원은 추론 자원과 별도 필드에 기록합니다. 기존 구체 클래스 계약으로 두 어댑터를 수용하며 추상 기반 클래스, 플러그인 레지스트리, 비동기 파이프라인은 만들지 않습니다. 마이크 입력은 향후 범위입니다.

스트리밍을 지원하지 않는 백엔드는 발화 단위 또는 창 단위 처리 능력을 그대로 표시합니다. 창 단위 반복 실행을 네이티브 스트리밍과 같은 의미로 보고하지 않으며 지원하지 않는 부분 자막 지표는 `null`과 사유를 남깁니다. SenseVoice의 실제 ARM64 batch 로딩·50개 개발 발화 처리·관측 메모리는 확인했지만 무음 오출력·실시간 결합·장시간 적합성은 별도 검증이 필요합니다. Qwen3-ASR은 미구현입니다.

## 단계별 완료 조건

1. 초기 골격: 소스 설치·설정 검사·문서·원본 무결성 확인 완료.
2. RP5 기준선: 반복 요약과 후속 사용자 전달 RP5 Codex 검사 보고에서 측정 90/90·워밍업 9회 성공, 통합·자산·환경 일치, 단위 테스트 35개 통과가 확인됐다고 보고됐습니다. 이번 단일 WAV에서는 2스레드를 잠정 기준값으로 유지합니다. [기록](../results/summaries/zipformer-repeat-20261028-reported.md)에 출처·한계를 명시하며 Host 직접 재실행이나 원본 파일 전송과는 구분합니다. 원시 결과는 RP5의 내부 백업에 보존합니다.
3. 공통 평가: manifest·CER·상주 Zipformer batch·paced_stream·CPU/RSS·재채점·고정 데이터 준비를 구현했습니다. Host 테스트 83개를 통과했고 RP5에서 KSS 개발용 50개를 [batch](../results/summaries/zipformer-kss-dev50-20261028.md)와 [1배속 모사](../results/summaries/zipformer-paced-kss-dev50-20261028.md)로 직접 실행·회수·검증했습니다. 모사 입력의 청크/부분 가설·파일 끝 지연·유한 큐 드롭·백로그를 계측합니다. 정답 시각 정렬·실제 마이크·장시간 서비스, 다화자·독립 평가 자료와 설정 비교는 남아 있습니다.
4. 모델 비교: M2-0/M2-1 구현 후 [M2-2 SenseVoice 실제 RP5 batch](../results/summaries/sensevoice-kss-dev50-20261028.md)를 확인했습니다. 같은 스레드/제한 조건의 비교·무음/VAD·독립 평가·실시간 결합은 남아 있습니다. Qwen3-ASR은 이후 검토합니다.
5. 실시간 배포: 마이크 캡처 큐, 드롭 정책, endpoint, 자막 소비자와 systemd 서비스, 장시간 안정성 검사.

사용자 제공 요약은 그 출처로만 기록하고 직접 실행·원시 자료 검증과 구분합니다. 새 공통 평가의 Host 모의 검사도 실제 RP5 모델 검증과 구분합니다. 다른 모델 비교·실시간 단계는 아직 계획입니다.
