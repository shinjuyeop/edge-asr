# WAV 실시간 자막 MVP

`live`는 WAV를 1배속 100ms 청크로 입력하고 터미널에 자막을 표시한다. 스피커 없이 SSH에서 사용할 수 있다. 기본값은 Zipformer 1T·SenseVoice 2T·온라인 Silero VAD 1T이며, 별도 환경에서 Qwen 2T를 보정 후보로 선택할 수 있다. 마이크 입력·스피커 재생은 별도다.

## 실행

RP5의 동기화된 clean main에서 실행한다. 모델 다운로드·패키지 설치는 하지 않는다. 기존 Zipformer·SenseVoice·Silero 가중치와 설정 파일이 필요하다.

```bash
cd /home/deepet/projects/edge-asr
.venv/bin/python scripts/edge_asr.py live \
  --wav models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/test_wavs/0.wav
```

입력은 16kHz mono PCM16 WAV다. 변환·음량 정규화는 적용하지 않는다. 결과는 `results/live/wav-<UTC>-<ID>/`에 저장한다. `--output <새 폴더>`로 지정할 수 있고 기존 폴더는 거부한다. 원음은 수정하지 않는다. Host에서 `ssh -t rp5`로 접속해 같은 명령을 실행할 수 있다. 소스는 기존 main push/pull 방식으로 동기화한다.

```bash
# 기존 정답·해시가 있는 개발 발화 여러 개
.venv/bin/python scripts/edge_asr.py live \
  --manifest data/evaluation/kss-dev50/manifest.jsonl
```

Qwen 후보는 준비된 **Qwen+VAD 환경**에서 명시적으로 선택한다. batch용 `.venv-qwen3-onnx-20261106-01`에는 Python VAD 런타임이 없으므로 아래 환경과 구분한다. 기존 환경에 패키지를 추가하지 않는다. Qwen·Zipformer·Silero 모델이 필요하고, 이 선택에는 SenseVoice 모델이 필요하지 않다.

```bash
.venv-qwen3-vad-20261106-01/bin/python scripts/edge_asr.py live \
  --refine-model qwen3 \
  --wav models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/test_wavs/0.wav
```

`--refine-model sensevoice`는 기본값과 같다. 두 후보 모두 Refine 2T·원음·vad0·최대 5초·문맥 0을 사용한다. Qwen은 검증한 모델 자산·한국어·KV 512·출력 128 설정을 유지한다. 선택한 모델은 시작 메시지와 원시 worker 설정에 기록된다. [같은 VAD 구간·동시 실행 검증](../research/qwen3_realtime.md)의 범위에서 사용하는 후보이며 더 높은 정확도를 모든 발화에 보장하지 않는다.

## 표시 정책

```text
[파일ID][Zip 임시] 현재까지의 빠른 전사
[파일ID][Zip 최종] 원본 WAV의 Zip 최종 전사
[파일ID][보정 대기 0.124~3.526초]
[파일ID][보정 후보·자동 적용 안 함 0.124~3.526초] 해당 구간의 선택한 보정 모델 결과
```

Zip은 원본 WAV마다 연속 상태를 유지한다. VAD가 방출한 최대5초 구간의 보정은 원본 ID·원본 시작 기준 구간을 붙여 별도 표시한다. 늦은 보정도 이전 원본 ID에 표시한다. Zip을 자동 덮어쓰지 않으며 두 모델 원문에 문구·반복·문장부호 필터를 적용하지 않는다. terminal 제어 문자는 화면에서만 escape한다. [M3-5의 KSS5개 악화](../../results/summaries/m35-guarded-splice-20261103.md)를 반영한 첫 정책이며 보정이 항상 더 정확하다는 뜻은 아니다.

## 실행 제한·기록

기존 `operational` 열 정책을 내부 적용한다. 53°C 이하·현재 제한 없음3회로 시작하고 RP5 기본 열 보호를 유지한다. 온도/감속은 관찰하며 저전압·계측 누락·외부 제한 실패는 실패로 남긴다. 주소 공간4GiB/프로세스·timeout600초·Refine2작업/capture5청크 큐를 유지한다. 첫 MVP의 입력 합계 상한은480초다. Ctrl+C는 guard의 자식 프로세스 그룹 정리 경로를 사용한다. SSH 단절 후에는 기존 PID·영수증부터 확인하고 부하를 중복 시작하지 않는다.

| 파일 | 내용 |
| --- | --- |
| `summary.json` | 표시 완료·지연·원시 계측 연결·미측정 항목 |
| `terminal-events.jsonl` | 부모 stdout 쓰기 시작/flush 완료와 표시 원문 |
| `input.jsonl` | `--wav` 입력의 해시. 빈 정답은 자리표시자 |
| `runtime/caption-events.jsonl`, `console.log` | worker 이벤트와 전달 원문 |
| `runtime/worker.json`, `summary.json` | 모델/환경/로딩·워밍업·추론·큐·CPU/RSS |
| `runtime/execution.json`, `thermal.jsonl` | 외부 제한·온도·실패 영수증 |

첫 Zip 표시 지연은 오디오 시작→부모 stdout flush, 보정 지연은 소유 구간 끝→후보 stdout flush다. 가용→stdout 값은 worker 모델 반환/실제 수신부터 부모 출력 완료까지이며 파이프 비용을 포함한다. **화면 렌더링·SSH 네트워크 도착·음소 기준 인식 지연은 null/미계측**이다. 모델 PID CPU/RSS는 기존 main/Refine 합으로 별도 터미널 부모/guard/producer 비용을 제외한다. batch RTF나 보정 적용 지연으로 부르지 않는다.

`--wav`는 정답이 없으므로 Zip CER는 null이다. 내부 공통 manifest의 빈 정답은 무음 정답이 아니다. 기존 정답을 준 `--manifest`에서만 `ko_cer_v1` Zip CER를 연결한다. 병합 전사가 없으므로 merged CER는 null이다. 원시 재집계·표시 원문/이벤트 대조를 완료해야 성공으로 처리한다. Host 모의 테스트와 RP5 실제 추론은 [실측 목록](../../results/summaries/README.md)에서 구분한다.
