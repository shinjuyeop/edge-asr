# M2-0 / M2-1: SenseVoiceSmall INT8 batch 통합

현재 완료 범위는 **Host 구현·mock 계약 검사**입니다. SenseVoice 가중치를 다운로드하지 않았고 ARM64 모델 로딩·추론·성능은 미검증입니다. M2-2의 실제 RP5 검증을 통과하기 전에는 M2 전체 완료·SenseVoice 정확도/속도 향상을 주장하지 않습니다. M1 설정·요약·음원·모델과 RP5 기존 `.venv`는 그대로 보존합니다.

## 어댑터와 출력 정책

`adapters/sensevoice.py`는 `load()`, `transcribe(Audio) -> str`, `close()`, `model_files` 계약을 사용합니다. [sherpa-onnx v2.0.1 Python API](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py)의 `from_sense_voice()`를 기준으로 구현했습니다. 모델은 한 번 로드하고 발화마다 `OfflineRecognizer.create_stream()` → `accept_waveform()` → `decode_stream()` → `stream.result.text`로 처리합니다. 모델별 분기는 factory와 설정 검증에만 있으며 공통 batch 루프에 SenseVoice 분기는 없습니다.

기본 설정은 `configs/sensevoice_eval.toml`의 배포 INT8 `model.int8.onnx`·`tokens.txt`, CPU 1스레드, `language="ko"`, `use_itn=false`, 16kHz입니다. 80차원 특징은 SenseVoice factory의 고유 규격으로 지정하며 Zipformer 설정에서 가져오지 않습니다. Zipformer의 tail padding·`input_finished()`·`is_ready()` 루프는 사용하지 않습니다. 짧은 음원 오류를 숨기기 위한 자동 padding·재시도도 없습니다. 실제 최소 입력 길이는 M2-2에서 확인합니다.

`sensevoice_control_tokens_v1`은 **SenseVoice 어댑터에서만** 예약 형태 `<|[A-Za-z][A-Za-z0-9_]*|>`를 분리하고 앞뒤 공백을 제거합니다. 일반 괄호·문장부호·내부 공백·숫자·대소문자는 바꾸지 않습니다. 앞으로 추가되는 같은 문법의 태그도 분리하되 raw 출력과 태그를 모두 보존합니다. 이는 단어·의미를 교정하거나 일반 텍스트를 삭제하는 공통 정규화가 아닙니다. sherpa가 이미 태그를 제거한 본문을 반환하면 태그 목록은 빈 배열입니다.

`predictions.jsonl`의 `hypothesis`는 전사 본문이며 선택 필드 `adapter_result`에 `raw_text`, `special_tags`, `output_policy`를 별도로 저장합니다. 워밍업 결과와 평가 발화 결과가 섞이지 않도록 각 호출마다 메타데이터를 갱신합니다. CER에는 본문만 사용합니다.

`sensevoice + paced_stream`과 아직 미구현인 Qwen 공통 평가는 설정 검사에서 모델 로딩·결과 폴더 생성 전에 거부합니다. `check-config` 성공은 설정 검사이며 자산·네이티브 추론 성공을 뜻하지 않습니다.

## 계측과 메타데이터

| 필드 | 범위 |
| --- | --- |
| `environment.json.runtime.sherpa_native_onnxruntime_version` | 모델 로딩 과정에서 import한 sherpa의 `onnxruntime_version` |
| `environment.json.runtime.python_onnxruntime_package_version` | 별도 Python 배포 패키지 버전. 위 값의 대체값으로 쓰지 않음 |
| `runtime.status/reason` | 런타임 미import·미설치·버전 미제공은 null과 사유. 0이나 Python ORT 버전으로 채우지 않음 |
| `manifest.model_load_s`, `model_load_cpu_s` | `adapter.load()`의 런타임 import·인식기 초기화 wall/주 PID CPU |
| `manifest.model_load_resources` | 위 로딩 단계의 주 PID RSS 100ms/호출 경계 관측. 실패 시에도 가능한 표본 보존 |
| `predictions.resources` | 모델 로딩·워밍업을 제외한 발화 추론 단계. 기존 지표 범위 유지 |
| `manifest.model_metadata` | 모델 설정과 어댑터의 언어·ITN·출력 정책·CPU/특징 규격·양자화 선언 |
| `manifest.model_files` | 실제 파일 경로·SHA-256. 파일 형식·정확도·양자화 dtype를 증명하는 값은 아님 |
| `manifest.unmeasured_reasons` | 정답 시각 정렬·Offline Refine·endpoint·화면 표시 등의 미계측 사유 |
| `manifest.process_id` | 실제 평가 Python PID. 스레드 진단 대상으로 사용 |

기존 `schema_version=1`과 기존 필드는 유지하고 선택 메타데이터만 추가했습니다. 과거 결과를 소급 변경하지 않습니다. 로딩 RSS·추론 RSS·입력 자식의 생애 최고치를 합쳐 동시 파이프라인 피크로 표시하지 않습니다. 관측 사이 피크는 놓칠 수 있습니다. 자산 체크섬을 모델 로딩 전에 읽기 때문에 `model_load_s`는 cold-storage 측정이 아닙니다.

`quantization=int8`은 사용할 배포 모델의 설정 선언입니다. 그래프 dtype를 검사한 결과가 아닙니다. Zipformer도 파일명에 따른 INT8 표기와 정밀도 미확인을 구분하여 기록합니다. 배포 모델의 정확한 양자화 알고리즘은 미검증이므로 `quantization_method=null`과 사유를 남기며 이 프로젝트에서 변환·재양자화를 수행하지 않습니다. 실제 배포 파일의 리비전·출처·라이선스·해시는 M2-2에서 확인합니다.

```bash
# Host의 기본 환경 조회: 추론 런타임을 import하지 않음
.venv/bin/python scripts/edge_asr.py env
# 명시적 probe: sherpa import와 네이티브 버전 조회만 수행. 모델 로딩/다운로드 없음
.venv/bin/python scripts/edge_asr.py env --probe-runtime
```

기본 설치에 런타임이 없는 Host에서는 native 버전이 null이어야 정상입니다. `env`의 종료 코드만으로 추론 가능성을 판단하지 말고 `runtime.status/reason`을 읽습니다. 새 후보 환경이 필요할 때만 `.[sensevoice]` 선택 의존성을 사용할 수 있으며 RP5 기존 `.venv`에 설치·변경하지 않습니다.

## 모델 간 비교 계약

- 동일한 WAV·정답 manifest·오디오 해시, 16kHz mono PCM16 입력과 같은 발화 집합을 사용합니다. 모델 고유 특징 추출은 어댑터/런타임 내부에서 수행하여 추론 시간에 포함합니다.
- `ko_cer_v1`의 NFC·공백/문장부호 제거·숫자/대소문자 유지와 문자 가중 corpus CER을 바꾸지 않습니다. KSS 정답은 기존 `expanded_script`입니다. 언어·ITN·태그 분리 정책을 함께 보고하고 숫자 의미를 맞추는 임의 후처리를 섞지 않습니다.
- batch RTF는 준비된 float PCM에서 새 인식 상태 생성부터 최종 텍스트 반환까지입니다. 입력 전달·특징 추출·decode·모델별 출력 정리와 해당 종료 처리를 포함합니다. 로딩·워밍업·WAV I/O/변환·채점·결과 쓰기는 제외합니다. 실패는 성공률·문자 커버리지와 함께 보고합니다.
- **Streaming 첫 부분 시점**, **Offline batch 처리 시간**, **실제 발화 종료 후 Refine 확정 지연**, **모델 로딩/초기화**는 서로 다른 지표입니다. batch 처리 시간을 첫 자막·Refine 지연으로 쓰지 않습니다. 미계측은 null/사유로 남깁니다. 기존 `summary.json`의 숫자·계산 계약은 유지합니다.
- KSS 50개는 단일 화자의 짧은 개발·회귀 자료입니다. 최종 모델 선정용 독립 평가 데이터·다화자·잡음·긴 발화는 이후 단계입니다. 현재 결과로 전체 한국어 정확도나 서비스 지연을 일반화하지 않습니다.

## CPU 324%와 경량 TID 진단

기존 324%는 추론 구간에 평균 약 3.24코어 분량의 주 PID CPU를 쓴 값입니다. `num_threads=2`는 프로세스 전체 코어 제한이 아닙니다. 세션별 ORT 풀·spinning 등은 가능한 원인이지만 **실제 TID 역할·원인은 아직 확정하지 않았습니다**. 기존 수치와 정의는 수정하지 않았습니다. [sherpa v2.0.1 세션 구현](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/online-zipformer-transducer-model.cc), [ORT 스레드 설명](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)을 참고합니다.

M2-2에서 평가가 이미 실행 중일 때 다른 터미널에서 실제 Python PID를 대상으로 관측합니다. timeout 래퍼의 PID를 대신 지정하지 않습니다.

```bash
# ASR_PID는 해당 실행 manifest.json의 process_id로 지정
.venv/bin/python scripts/edge_asr.py threads --pid "$ASR_PID" \
  --duration-s 3 --interval-s 0.1 \
  --output results/diagnostics/고유한-실행이름.json
```

이 명령은 Linux `/proc/PID/task/TID/stat`을 읽습니다. 모델을 시작하거나 affinity·스레드 수·우선순위를 바꾸지 않습니다. CPU는 동일한 TID 시작 시각으로 연속 관측한 두 표본의 CPU tick 차분입니다. 중간 생성/소멸 경계는 누락될 수 있으며 PID 재사용·종료를 따로 표시합니다. 스레드 수가 많다는 사실만으로 활성 스레드·spinning 원인을 확정하지 않습니다. ORT 세션별 역할은 null/사유로 남깁니다. 관측 자체의 오버헤드가 있으므로 성능 기준선과 진단 실행은 구분합니다. 기본 평가에서 자동 활성화하지 않습니다.

## M2-2 RP5 실제 검증 순서 — 이번 작업에서 실행하지 않음

1. 기존 RP5 `.venv`·모델·데이터·과거 결과와 Git 상태를 확인하고 보존합니다. Host와 RP5 모두 main을 사용하며 RP5의 main을 fast-forward로 업데이트합니다. 강제 reset은 사용하지 않습니다.
2. [sherpa 배포 SenseVoice INT8 모델](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)의 고정 버전을 새 모델 폴더에 준비합니다. 기본 경로는 `models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/`이며 `model.int8.onnx`, `tokens.txt`가 필요합니다. 출처·리비전·라이선스·해시를 보관하고 기존 Zipformer 파일은 건드리지 않습니다.
3. 기존 `.venv`로 `env --probe-runtime`과 `check-config --evaluation configs/sensevoice_eval.toml`을 실행합니다. 패키지가 부족하면 기존 환경을 수정하는 대신 새 후보 환경을 별도로 준비합니다.
4. 아래 외부 제한 절차로 기존 공식 WAV 한 개부터 batch 실행합니다. 모델 로딩·메모리·언어/ITN·태그/본문·짧은 입력 실패를 확인합니다. Host mock 통과를 이 단계의 성공으로 대체하지 않습니다.
5. `data/evaluation/kss-dev50/manifest.jsonl`로 확대하고 성공/실패·커버리지·CER·RTF·로딩/추론 RSS·CPU를 기록합니다. 필요하면 별도 실행에서 TID를 관찰합니다.
6. 원시 결과를 Host로 회수해 체크섬·재채점·동일 입력 조건을 확인합니다. 실제 성공한 범위만 새 검증 기록으로 추가합니다. paced_stream·Refine·마이크·M2 전체 완료는 별도 판정입니다.

1번의 소스 업데이트는 다음과 같습니다. Git 변경이 있으면 먼저 보존하고 충돌을 확인합니다. Host 개발·커밋·push도 main에서 수행하며 별도 feature 브랜치는 만들지 않습니다. 소스 업데이트 자체는 SenseVoice 실제 추론 성공을 의미하지 않습니다.

```bash
cd /home/deepet/projects/edge-asr
git status --short
git switch main &&
git pull --ff-only origin main
```

## 외부 시간 제한과 실패 기록

다음은 **M2-2에서 사용할 절차**이며 이번 단계의 RP5 실행 결과가 아닙니다. GNU `timeout`을 외부에서 적용하므로 평가 루프에 복잡한 프로세스 감독기를 넣지 않습니다. 제한 시간 120초는 예시 예산이며 예상 모델 성능이 아닙니다. 전체 50개에는 예산을 별도로 정합니다. `--foreground`는 사용하지 않으며 종료 시 입력 자식을 포함한 같은 프로세스 그룹을 제한합니다.

```bash
cd /home/deepet/projects/edge-asr
mkdir -p results/executions
execution_dir=$(mktemp -d results/executions/sensevoice-XXXXXXXX)
.venv/bin/python - "$execution_dir" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
p = Path(sys.argv[1]) / 'execution.json'
p.write_text(json.dumps({'status': 'running', 'started_at_utc': datetime.now(timezone.utc).isoformat()}, indent=2))
PY
if timeout --signal=TERM --kill-after=5s 120s \
  .venv/bin/python -u scripts/edge_asr.py evaluate \
  --config configs/sensevoice_eval.toml \
  --manifest data/evaluation/official-test0.jsonl \
  --output-dir "$execution_dir/evaluations" \
  > "$execution_dir/console.log" 2>&1; then
  evaluation_exit=0
else
  evaluation_exit=$?
fi
.venv/bin/python - "$execution_dir" "$evaluation_exit" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
p = Path(sys.argv[1]) / 'execution.json'
record = json.loads(p.read_text())
code = int(sys.argv[2])
record.update(exit_code=code, finished_at_utc=datetime.now(timezone.utc).isoformat(),
              status={0: 'completed', 124: 'timeout', 137: 'killed_cause_unverified'}.get(code, 'failed'))
p.write_text(json.dumps(record, indent=2) + '\n')
print(p, record['status'], 'exit_code=', code)
PY
test "$evaluation_exit" -eq 0
```

위 `timeout`은 wall time 제한이며 메모리 상한은 아닙니다. M2-2에서 사용자 systemd/cgroup 메모리 제한을 먼저 확인할 수 있다면 위 `if timeout ...`을 `if systemd-run --user --scope --quiet --property=MemoryMax=4G --property=MemorySwapMax=0 timeout ...`으로 감싸 같은 receipt 절차를 사용합니다. 4G는 예시 보호 예산이며 필요 메모리의 예측값이 아닙니다. 실제 평가 PID의 cgroup과 적용된 상한을 실행 중 확인해야 합니다. 해당 장비의 사용자 scope 권한·제한 적용은 이번에 검증하지 않았으며 적용 실패 시 이를 기록하고 무제한 실행으로 자동 재시도하지 않습니다.

네이티브 hang·SIGKILL/OOM에서는 내부 manifest가 `running`인 채 남을 수 있습니다. 외부 receipt·console·마지막 progress를 함께 보관하고 미완료를 성공 표본으로 집계하지 않습니다. 137은 SIGKILL이며 timeout 후 강제 종료·OOM·외부 kill 중 어느 원인인지 종료 코드만으로 확정하지 않습니다. 커널 기록 등 증거 없이 OOM이라고 표시하지 않습니다. SSH/전원 단절로 receipt가 `running`이면 외부 기록도 미완료입니다. 기존 내부 결과를 수정해 성공/실패를 꾸미지 않습니다.
