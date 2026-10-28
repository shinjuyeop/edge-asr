# 공통 1배속 스트리밍 모사 평가

WAV를 한꺼번에 넣는 batch와 달리, 청크의 마지막 샘플이 도착하는 시각까지 기다린 후 입력합니다. 스피커 소리·마이크 없이 **음성이 흘러가는 동안 추론하는 1배속 replay**입니다. 실제 마이크·자막 화면·서비스 배포는 별도 단계입니다. 데이터·CER·모델 로딩·실패 보존·재채점은 [공통 평가](evaluation.md)를 재사용합니다.

## 실행

Host의 소스를 push하고 RP5에서 같은 커밋을 pull한 뒤, Host에서 SSH로 실행합니다. KSS 준비·전달은 [데이터 안내](datasets.md)를 따릅니다. 모델·가상환경·기존 데이터는 그대로 사용합니다.

```bash
ssh rp5 'cd /home/deepet/projects/edge-asr &&
  .venv/bin/python scripts/edge_asr.py check-config --evaluation configs/zipformer_paced.toml &&
  .venv/bin/python scripts/edge_asr.py evaluate \
    --config configs/zipformer_paced.toml \
    --manifest data/evaluation/kss-dev50/manifest.jsonl'
```

새 `zipformer_paced.toml`은 공통 평가용입니다. 기존 `zipformer_streaming.toml`은 옛 탐색 설정이며 보존 프로브·새 실행기에 대신 전달하지 않습니다. 모드는 결과의 `mode=paced_stream`으로 기록합니다. 기본 `evaluate` 설정은 계속 batch입니다.

기본값은 CPU Zipformer, 2스레드, 100ms 청크, 대기 큐 5개(명목 500ms), `drop_newest`입니다. 실제 마지막 청크는 100ms보다 짧을 수 있습니다. 모델은 한 번 로드하고 최초 유효 WAV로 대기 없는 청크 입력·flush를 3회 워밍업한 뒤 발화마다 새 스트림을 만듭니다. 각 파일은 1회 측정하며 입력 속도는 1배로 고정합니다.

## 입력·큐·추론

독립 입력 프로세스는 이미 준비된 PCM의 프레임 범위만 예정 시각에 공개합니다. 실제 PCM은 추론 프로세스에서 해당 범위를 읽습니다. 입력 시각은 `t0 + 청크 끝 프레임 / sample rate`에 고정하므로 앞 청크의 추론 시간만큼 다음 입력을 늦추지 않습니다. 큐가 차면 **새 청크를 버리고** 다음 예정 입력을 계속합니다. 버린 청크의 샘플 수와 오디오 초를 기록하며 무음을 대신 삽입하지 않습니다.

입력과 네이티브 추론이 Python GIL을 공유해 입력이 함께 멈추는 문제를 피하려고 별도 프로세스를 사용합니다. 모델 런타임의 스레드를 복제하지 않도록 [`multiprocessing`의 `spawn`](https://docs.python.org/3.11/library/multiprocessing.html#contexts-and-start-methods)으로 시작하고, 준비 handshake와 스트림 생성 뒤 오디오 시계 `t0`를 시작합니다. 프로세스 시작 시간은 replay 지연에서 제외하고 스트림 생성 비용은 처리 RTF에 포함합니다. CLI 파일·설치된 진입점·`python -m edge_asr`로 실행하며, `spawn` 특성상 파일 없는 `python -`/노트북에서 직접 API를 호출하는 방식은 사용하지 않습니다.

청크의 `accept_waveform` → 가능한 decode → `get_result`가 끝나 **호출자에게 가설이 반환된 직후** 시각을 기록합니다. 마지막 오디오를 배출한 뒤 0.66초 합성 무음을 대기 없이 전달하고 `input_finished`와 decode·최종 결과 반환을 수행합니다. 자동 endpoint나 실제 발화 끝 검출은 하지 않습니다.

## 지표의 의미

| 지표 | 이번 구현의 측정 범위 |
| --- | --- |
| CER | 전체 정답과 최종 가설, batch와 동일한 `ko_cer_v1`. 큐 드롭이 있으면 손실을 포함한 결과로 채점 |
| 처리 RTF | 스트림 생성 + 청크별 PCM 전달·특징 추출·decode·가설 반환 + 최종 flush의 활성 wall time 합 / 실제 오디오 길이. 모델 로딩·워밍업·입력 대기·IPC/큐 대기·계측 파일 쓰기 제외 |
| replay 경과 비율 | `t0`~최종 반환 / 오디오 길이. 1배속 입력 대기를 포함하므로 처리 RTF와 구분 |
| 첫 부분 시점 | 최초 비어 있지 않은 **청크 결과 반환**~`t0`. 청크 수집·큐 대기·모델 lookahead 포함. flush에서만 텍스트가 나오면 `null`과 사유 |
| 최종 확정 지연 | 최종 가설 반환~`t0 + 원본 파일 길이`. 입력 끝 기준 강제 flush이며 발화의 음향적 끝·endpoint 지연이 아님 |
| 청크 처리 | PCM 범위 복사·전달~해당 결과 반환. 대기 큐·입력 대기 제외 |
| 큐 대기 | 성공한 enqueue 호출 시작~dequeue 반환 경계. IPC 전달 포함 |
| 최신 청크 끝→반환 | 가설을 조회한 청크의 예정 입력 끝~반환. 해당 텍스트의 정답 음향 시각과 정렬한 실제 인식 지연이 아님 |
| 드롭 | 입력 큐에서 새 청크를 거부한 횟수·실제 잃은 프레임/sample rate. 마이크 하드웨어 overrun 미계측 |
| 백로그 | enqueue 성공 뒤 아직 dequeue되지 않은 청크의 실제 오디오 초·개수·최고령 대기 시간. 처리 중인 청크 제외 |
| CPU | `process_cpu_s`는 활성 호출 구간의 주 PID CPU. 별도로 `t0`~최종 반환의 **대기 포함 replay CPU**를 집계. 코어 하나가 100% |
| RSS | 모델·계측·이벤트를 가진 주 PID의 100ms 및 호출 경계 관측 최대. 입력 자식의 시작 포함 생애 RSS 최고치는 별도 기록하며 합산해 동시 피크로 주장하지 않음 |

실제 큐의 enqueue/dequeue 시각으로 100ms 격자와 오디오 끝·반환 경계의 백로그를 재구성합니다. 격자가 짧은 피크를 놓칠 수 있어 **모든 큐 이벤트의 최대**도 별도 보고합니다. 스레드별 `qsize()` 추정값을 쓰지 않습니다. 오디오가 짧으면 백로그·지연 분위수의 n도 작아집니다.

첫 부분·최종 지연은 성공 발화별, 청크 지연·큐 대기는 실제 처리 청크별, 백로그는 100ms 격자와 경계별 집단으로 nearest-rank p50/p95를 계산합니다. 분위수마다 n·최대를 기록하고 유효 n<2이면 분위수는 `null`입니다. 부분 가설 없는 성공 발화 수를 따로 집계합니다. 실패·미실행을 성공 지표에서 제외하므로 성공률·정답 문자 커버리지·포함 발화 수도 함께 읽습니다. 완전한 성공 계측이 없으면 드롭도 `null`이며 0으로 채우지 않습니다.

**정답 문자/구간의 음향적 끝 시각이 없으므로 실제 인식 지연은 계속 `null`입니다.** 첫 부분 시점에서 청크 길이 등을 빼서 만들지 않습니다. 자막 화면 전달·실제 마이크·자발화 endpoint·장시간 안정성은 아직 검증 대상이 아닙니다.

## 결과·재채점·중단

기존 `results/evaluations/eval-.../`에 환경·manifest·predictions·scores·summary·진행 기록을 남깁니다. 입력 자식·부분 가설·백로그 이벤트는 추가 경로에 보존합니다.

```text
events/00001/producer.jsonl   # 청크 예정/실제 입력·enqueue/drop·입력 완료/오류
events/00001/decoder.jsonl    # dequeue·청크별 가설 반환·최종 반환
events/00001/backlog.jsonl    # 실제 큐 이벤트의 100ms 격자 재구성
```

경로는 발화 순번으로 만들고 외부 manifest ID를 파일 경로로 사용하지 않습니다. 원본 전사·가설·자원 표본·모든 이벤트는 Git 제외입니다. predictions에는 재채점에 필요한 지연 표본·드롭·백로그 요약도 있으므로 모델·WAV·events 없이 기존 `score`로 재집계할 수 있습니다. batch와 paced_stream 결과를 섞으면 거부합니다.

온도와 CPU0 현재 클럭도 주 PID RSS 표본과 함께 관측하며 sysfs를 읽을 수 없으면 `null`입니다. 명목 간격은 100ms이고 실제 스케줄 지연·표본 간 피크 누락을 보증하지 않습니다. 보드 설정·팬·governor를 자동 변경하지 않습니다.

추론 예외·Ctrl-C에서는 입력 자식을 취소·회수하고 부분 이벤트를 보존합니다. 입력 완료 후 기본 10초의 큐 배출 제한과 입력 자식 준비 제한이 있습니다. 이 제한은 **네이티브 추론 함수 자체를 강제 종료하는 하드 타임아웃은 아닙니다.** 네이티브 hang·OOM·SIGKILL·전원 차단에서는 정리·최종 요약이 보장되지 않으므로 manifest 상태와 마지막 진행 기록을 확인합니다. `completed`여도 큐 드롭이 있을 수 있으므로 드롭 수와 오디오 초를 별도로 확인합니다.
