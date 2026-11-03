# M3-4 연속 Zip의 native 토큰 시각과 VAD 경계 진단

2026-11-03 사전 계획. [구간 reset 실측](../../results/summaries/m33-segment-merge-20261103.md)에서 Zip CER/첫 부분이 회귀했다. 이번에는 **기존 파일별 연속 Zip의 청크·상태·tail을 유지**하고, 토큰/시각/원문과 실제 Refine 수신의 구조 진단만 추가한다. 원문을 교체하지 않으며 교체 CER/실제 병합 적용 지연은 null이다. 운영 기본값은 유지한다.

## 확인한 API와 해석

RP5 설치 런타임은 sherpa-onnx2.0.1, git SHA `af2d8d81`, native ORT1.28.2다. Python wrapper의 `get_result()`는 문자열을 돌려주지만 내부 `recognizer.get_result()`의 native 객체에는 text/tokens/timestamps/start_time/segment가 있다. JSON 직렬화의 반올림을 거치지 않고 필드를 직접 복사한다. 추가 decode/endpoint reset을 하지 않는다.

공식 [결과 형식](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81/sherpa-onnx/csrc/online-recognizer.h)은 토큰이 디코드된 시점을 정의한다. [transducer 변환](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81/sherpa-onnx/csrc/online-recognizer-transducer-impl.h)의 시각은10ms×4 격자이며 [greedy decoder](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81/sherpa-onnx/csrc/online-transducer-greedy-search-decoder.cc)는 프레임 오프셋을 누적한다. 실제 설치 결과에서 원점/증분/유효 범위/접두부 안정성을 확인한다. 이 값은 **토큰의 음향 시작·끝 구간이나 사람 검수 단어 경계가 아니다.**

원문 재구성은 현재 Unicode BPE 범위에서 [symbol 변환](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81/sherpa-onnx/csrc/symbol-table.cc)·[CJK 표시 공백 규칙](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81/sherpa-onnx/csrc/text-utils.cc)과 wrapper strip을 대조한다. 각 표시 문자의 원래 토큰 인덱스를 추적한다. Byte fallback/알 수 없는 변환·시각 원점·재구성 불일치는 수정해 통과시키지 않고 보류한다. 기존 ASR 원문·CER 정규화는 변경하지 않는다.

## 고정 진단 정책

- 스냅샷은 실제 Zip partial/final 반환 직후 읽고, 모델 반환·native 읽기·진단 시각을 구분한다. 본문·토큰·시각 원시를 보존한다. 이 관측/기록 비용은 새 조건에 포함되며 과거와 동일 CPU 조건으로 포장하지 않는다.
- 토큰과 시각의 길이/유한성/정렬·공급된 입력+명시적 tail 범위, 원점0/segment0, 원문 재구성, 이전 토큰/시각 접두부를 검사한다. 원본별 최초 오류 뒤에는 해당 원본의 대응을 보류한다. 전체 파일은 원래 연속 Zip으로 계속 인식한다.
- VAD 소유 범위를 native **point**의 반열린 범위로 대응한다. 양쪽 이웃 시각과 토큰/표시 문자 범위를 남긴다. 오른쪽 문맥이 아직 없으면 기다리며, EOF는 명시적으로 구분한다. BPE 단어 이어짐을 자르는 경계·빈 범위·padding 안의 문맥은 보류한다. raw float와40ms 구현 격자의 절단 인덱스가 다르면 반올림해 고치지 않고 모호성으로 보류한다.
- Refine은 기존2작업 큐로 받고 실제 parent 수신을 기록한다. 큐 드롭·실패·미반환은 후보로 승격하지 않는다. source/job/소유 좌표/payload 해시를 대조한다. 정답은 진단 상태에 전달하지 않는다.
- 조건을 만족해도 명칭은 `structural_candidate`다. 음향 정렬·교체 안전성이 검증됐다고 표시하지 않는다. 이후 스냅샷/EOF에서도 같은 범위를 유지하는지, 후보의 파일 종료 전 비율·추가 대기·철회/변화를 확인한다. **실제 교체는0**, UI·마이크·Hybrid 배포는 실행하지 않는다.

## 실행과 완료 조건

Host 전체 회귀와 과거 원시 재집계 → main push/RP5 pull → RP5 회귀 후 아래 순서로 각1회 실제1배속 측정한다.

| 순서 | 기존 입력 | 확인 |
| --- | --- | --- |
| 1 | KSS 개발50개 | 연속 CER/첫 부분 유지·원문/접두부·짧은 구간 |
| 2 | 합성120초1개 | 장문 시간 원점·VAD 강제 분할·늦은 문맥/철회 |
| 3 | 디지털무음6개 | 빈 토큰·VAD 비호출·null 지연 |

Zip1T+Sense2T+VAD1T, 원음/vad0/100ms 입력·5청크 큐, 모델별 워밍업3회, Refine2작업 큐를 유지한다. 같은 WAV/정답·기존 모델·가상환경·데이터·이전 결과는 보존한다. 기존 `operational` 열 보호·실제 main/Refine 각각4GiB·외부600초 예산 아래 실행한다. 저전압/계측 누락/실행 제한 실패/네이티브 실패는 중단한다. 고정 클럭 통제/독립 최종 평가·새 양자화는 아니다.

새 ID는 `m34-continuous-token-20261103-01`; 원시는 `results/preflight/`, 계획/실행/전후 보존·검증 로그는 `results/maintenance/` 아래 같은 ID다. 모든 스냅샷/작업/수신/진단 이벤트의 정합성과 Host/RP5 모델 없는 재집계를 확인한다. CER·실제 첫 부분·CPU/RSS/TID·열·큐/드롭과 구조 진단 실패/후보 수를 함께 보고한다. 결과가 부정적이어도 기존 기록을 숨기거나 규칙을 사후 조정하지 않는다.

진단 데이터의 구조가 유효하다는 것과 실제 교체의 정확성을 구분한다. 후보가 있어도 별도 검증 없이 자동 교체를 채택하지 않는다. 자연 발화 경계/음향 정렬·연속 자막 보존·마이크/UI·장시간 운영은 다음 조건이다.

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --temperature-policy operational --output <새_폴더> -- \
  .venv/bin/python scripts/edge_asr.py tools probe-concurrent \
  --manifest data/evaluation/kss-dev50/manifest.jsonl \
  --zip-threads 1 --refine-threads 2 --refine-policy vad0 \
  --token-diagnostics --output <같은_새_폴더>

.venv/bin/python scripts/edge_asr.py tools summarize-token-alignment \
  --run <완료된_원시_폴더> --output <별도_재집계.json>
```

## 실행 기록

소스 `fcc942a`의 [RP53회 결과](../../results/summaries/m34-continuous-token-20261103.md)를 확인했다. 연속 Zip/Refine 전사와 VAD PCM 불변·첫 부분 유지,3,119개 native 스냅샷 유효/접두부 수정0,34구조 후보/66보류였다. 원문 교체는0이며 경계 기준을 사후 완화하지 않았다. Host/RP5 각267개 회귀·원시66개 재집계·기존5,308개 파일/환경 보존을 확인했다. 다음은 후보 밖 전사를 보존한 모델 없는 교체 재생이며, 음향 정렬/실제 교정/UI/마이크 성공으로 표시하지 않는다.
