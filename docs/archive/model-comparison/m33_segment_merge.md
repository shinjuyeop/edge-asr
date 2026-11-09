# M3-3 같은 VAD 구간의 Streaming/Refine 병합 실험

2026-11-03 사전 계획. [M3-2 파일 단위 선택](../../../results/summaries/m32-merge-replay-20261103.md)은 구간 ID를 안전하게 처리했지만 장문 중간 교정을 약118초 미뤘다. 이번에는 별도 `--segment-merge` 모드에서 **실제 1배속 WAV/VAD 입력 도중 구간별 Zip partial과 Refine 반환을 parent 상태에 적용**한다. 기존 기본 모드·모델·데이터·환경은 유지한다. UI·마이크는 실행하지 않는다.

## 경계와 모델 입력

현재 RP5 sherpa-onnx 2.0.1의 Python `get_result()`는 문자열만 돌려주고 내부 결과에는 tokens/timestamps가 있다. 하지만 내부 시각의 안정성·단어 경계 의미·부분 문자열 연결 정책은 검증하지 않았다. 이번에 누적 문자열을 시간 비율로 나누거나 이를 강제 정렬 정답으로 사용하지 않는다.

Zip은 VAD 소유 구간마다 새 stream을 사용한다. 실제 VAD의 가장 긴 종료 무음4800프레임(300ms)만큼 활성 입력을 보류하고, 그 이전의 확정 가능한 PCM을100ms씩 처리한다. 자연 종료 시 이미 처리한 프레임이 끝 경계를 넘으면 실패한다. 5초 강제 분할/EOF에서는 남은 실제 PCM을 보충한 뒤 기존 Zip tail0.66초와 input_finished로 마감한다. 짧은 구간은 VAD 방출 전 partial이 없을 수 있으며 개수/시점을 그대로 기록한다.

문맥 없는 `vad0`만 허용한다. 두 모델의 소유 프레임·float32 PCM·해시가 정확히 같아야 한다. 과거 연속 Zip은 VAD 밖의 음성도 모두 받지만 이번 경로는 VAD 미검출 음성을 복구하지 못한다. 상태 reset·300ms 보류·구간별 tail은 이전 연속 모델과 다른 조건이며 정확도/지연 악화를 숨기지 않는다. 후속200ms·새 정규화·모델/양자화 변경은 이번 실험에 추가하지 않는다.

활성 구간의 end는 아직 미정이다. 병합기에 실제 끝처럼 미래 상한을 넣지 않고 `end=null`/별도 `end_bound`로 표시한 뒤 VAD 방출 때 확정한다. 다음 구간 등록 전에 이전 구간의 소유 끝은 확정되지만 Refine은 나중에 올 수 있다. 세션·원본·구간·작업 ID, 소유/문맥 좌표와 PCM 해시로 늦은 결과가 다른 구간을 덮지 않게 한다.

## 실행 중 선택과 계측

같은 실제 모델 출력으로 `zip_only`/`refine_complete`/`agreement_only` 세 상태기계를 동시에 실행한다. 정답은 병합기에서 사용하지 않는다. 각각의 실제 parent 적용 시각을 기록하고 선택은 M3-2와 같은 규칙을 쓴다. 공백 연결 외 중복 제거·임의 금칙어·사후 유리한 모델 선택은 없다. 운영 기본값으로 자동 채택하지 않는다.

기존2작업 Refine 큐를 유지하며 자식은 인식 종료 후 원시를 쓰고 작은 결과 메시지를 Pipe로 보낸다. 전용 reader가 실제 `received_ns`를 기록하고 단일 lock으로 상태 적용을 직렬화한다. Zip native 호출 중에도 결과를 수신할 수 있고, lock 대기는 별도로 측정한다. 입력 프로세스의 유한 큐/드롭은 유지한다. 무한 서비스의 취소/축출/재접속 프로토콜을 구현하지 않는다.

- 입력 오디오 시작→첫 Zip partial과 소유 구간 시작→첫 partial을 구분한다. VAD 방출 전 첫 partial이 나온 구간 수도 기록한다.
- VAD 소유 끝→방출→작업 제출→child 시작/종료→실제 parent 수신→병합 적용을 분리한다. 마지막 적용은 메모리 상태 전환 시각이며 UI 렌더링/음향 정답 끝 지연은 아니다.
- 원본 파일 끝 전 확정된 구간 수와 Refine 종료→해당 구간 선택을 기록해 파일 끝까지 기다리는 문제가 해결됐는지 본다. 원본 전체 CER는 소유 순서로 연결하고 `ko_cer_v1`로 채점한다. 구간별 정답 경계는 없으므로 구간별 CER를 만들지 않는다.
- 원본별 개선/악화·빈 정답의 비어 있지 않은 출력, 원문 변경/정규화 접미부 수정량, CPU/RSS·TID·온도/스로틀링·큐/드롭을 함께 남긴다. 새 모드의 호출 RTF를 독립 batch RTF로 해석하지 않는다. 미호출/미측정은 null이다.

원시 이벤트 재생으로 실제 결정/수정 이벤트/최종 상태를 일치 검증한다. Zip/Refine 원시와 병합 입력도 교차 확인하며 모델 없이 재집계한다.

## 고정한 실행과 완료 조건

Host 회귀 → main push/RP5 pull → RP5 회귀 → 아래 각1회 순서로 실행한다.

| 순서 | 기존 입력 | 목적 |
| --- | --- | --- |
| 1 | KSS 개발50개 | 구간 reset의 CER 회귀·첫 partial·개별 악화 |
| 2 | 기존 합성120초1개 | 강제 분할·파일 끝 전 교정·계속 입력 중 반환 |
| 3 | 기존 디지털0 무음6개 | VAD 비호출과 빈 출력/null 지연 |

Zip1T+Sense2T+VAD1T, 원음,100ms 입력·5청크 큐, Refine2작업 큐, 모델별 워밍업3회다. 세 선택 정책은 모델을 세 번 실행하는 비교가 아니며 같은 출력의 실제 메모리 상태 적용3개다. 반복/독립 평가와 구분한다. 기존 `operational` 열 보호/계측과 실제 PID4GiB·외부600초 예산을 유지한다. 저전압/계측 결측·네이티브 실패/안전 제한 실패는 중단하며 통제 클럭 비교라고 표시하지 않는다.

새 ID는 `m33-segment-merge-20261103-01`, 원시는 `results/preflight/`, 계획/로그/전후 보존 감사는 `results/maintenance/` 아래 같은 ID다. 기존 원시·가중치·가상환경을 변경하지 않는다. 입력/Refine 손실·미완료는 실패/불완전으로 기록하고 성공한 부분만으로 전체 CER를 만들지 않는다.

Host 모의 PCM·종료 경계/강제 분할·늦은/역순 반환·무음·실패/회수·실제 IPC·원시 재집계와 기존 전체 테스트 통과가 선행 조건이다. RP5에서는 두 모델의 실제 입력 해시 일치·parent 수신/병합 적용·손실/정합성/환경 보존을 확인해야 완료다. 정확도/지연이 나빠도 결과를 보존하고 자동 정책을 채택하지 않는다. 자연 장문·마이크·UI·사람 검수 경계·장시간 운영은 별도다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_폴더> -- \
  .venv/bin/python scripts/edge_asr.py tools probe-concurrent \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --zip-threads 1 --refine-threads 2 --refine-policy vad0 \
  --segment-merge --output <같은_새_폴더>

.venv/bin/python scripts/edge_asr.py tools summarize-segment-merge \
  --run <완료된_원시_폴더> --output <별도_재집계.json>
```

## 실행 기록

소스 `05c3e7b`로 [RP53회 실제 결과](../../../results/summaries/m33-segment-merge-20261103.md)를 확인했다. 100구간의 동일 PCM·실제 parent 수신/상태 적용·손실0과 Host/RP5 각255개 회귀·기존5,210개 파일 보존을 검증했다. 합성42/43구간은 파일 종료 전 확정됐지만 구간 Zip의 CER/첫 부분이 회귀했다. 기본값으로 채택하지 않으며 다음 진단은 연속 Zip 상태를 유지하는 token 시각/경계 대응이다. UI·마이크·Hybrid 배포 완료를 의미하지 않는다.
