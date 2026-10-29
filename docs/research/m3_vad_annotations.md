# M3 VAD 구간 주석과 예비 비교

이번 도구는 배포 라벨을 이용한 **예비 합치율**과 사람이 청취·파형 검수를 마친 **정답 평가**를 분리한다. 전사 CER, Streaming 첫 자막, Refine 지연을 측정하는 실행기가 아니다. 기존 ASR/VAD 결과는 변경하지 않는다.

## 새 입력과 고정 계획

기존 공개 검수 WAV 98개를 그대로 참조한다. 원본 FLAC/WAV나 이전 검수 패킷은 수정하지 않는다. `prepare_vad_annotations.py`가 새로운 `annotations.jsonl`과 `provenance.json`을 만든다.

- 개발: 32명 화자의 짧은 발화 64개·긴 발화 11개와 미검수 잡음 5개, 총 80개.
- 보류: `s05/s10/s15/s20/s25/s30/s35/s40`의 짧은 발화 16개·긴 발화 2개. 원래 화자 그룹별 5번째 ID를 고정했으며 VAD 결과로 선택하지 않았다. 이 18개는 워밍업에도 사용하지 않는다.
- 이번 프로젝트의 보류 집합일 뿐 모델 학습 중복이 없다는 보장은 아니다. 최종 평가 정책과 용도는 별도 확정한다.
- 1스레드 Silero, threshold 0.5, 최소 음성 0.1초, 종료 무음 0.3초, native soft limit 5초, 외부 소유 상한 5초, native 활성 예산 30초를 유지한다.
- 원음/인과적 RMS26 각 1회, 첫 개발 초단문으로 정책별 워밍업 1회. 모델은 정책별 프로세스에서 한 번 로드한다. 원음→RMS 순서이며 무작위 교차 실험이 아니다.
- 100ms 블록·512프레임 VAD 변환을 재사용한다. 기존 실험의 합성 앞뒤 무음은 추가하지 않는다. **공개 WAV의 자연 문맥부터 상태를 초기화**하므로 이전 KSS/연속 입력 실험과 조건이 다르다.
- 빠른 파일 공급만 실행한다. 실제 입력 큐·마이크·1배속 지연은 측정하지 않는다. 기존 고부하 냉각 실패를 해결했다는 뜻도 아니다.

## 주석 상태와 검수 결정

모든 시각은 최종 16kHz WAV의 정수 프레임, `[start_frame,end_frame)`이다. `regions`는 WAV 전체를 틈·겹침 없이 덮는다. 각 구간에 `speech`, `non_speech`, `ignore`와 판정 사유를 기록한다.

| 상태 | 용도 |
| --- | --- |
| `corpus_provisional` | Seoul 배포 음소 라벨의 투영. 원본 라벨·절취 프레임·해시 추적, 프로젝트의 청취 검수 미완료 |
| `unreviewed` | 잡음 등 음성 여부 미확인. 전 구간 `ignore` |
| `human_reviewed` | 검수자가 청취·파형을 확인하고 이름/식별자·시간대 있는 검수 시각·검수 설명을 명시 |
| `excluded` | 제외 사유 보존, 전 구간 `ignore`, 추론 목록에서도 제외 |

Seoul의 [배포 문서/논문](https://www.openslr.org/113/) Table 3에 있는 36개 음소 표기는 예비 `speech`, `<SIL>`만 예비 `non_speech`로 매핑한다. `<IVER>`, `<VOCNOISE>`, `<LAUGH-…>`, `<NOISE>`, `<UNKNOWN>`, `<PRIVATE.INFO>`와 미등록 표기는 전부 `ignore`다. **면접자 발화·웃으면서 한 말을 무음으로 바꾸지 않는다.** 누락한 소리를 자동으로 무음 정답으로 채우지 않는다.

다음 명령은 형식과 범위를 검사할 뿐 청취 검수를 대신하지 않는다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools review-annotations \
  --annotations data/review/<주석_ID>/annotations.jsonl
```

실제 검수 후 작성할 결정 JSONL에는 `id`, 해당 `audio_sha256`, `annotation_status`, `reviewer`, `reviewed_at`, `review_method="listening_and_waveform"`, `review_note`, `regions`, `endpoint_reference_frame`를 넣는다. 제외라면 `exclusion_reason`도 필요하다. `endpoint_reference_frame`은 미확정이면 null로 유지한다. 음원 경로·화자 분할·PCM 해시는 결정 파일에서 바꿀 수 없다. 여러 차례의 부분 검수도 새로운 출력으로 누적할 수 있다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools review-annotations \
  --annotations data/review/<기존_주석_ID>/annotations.jsonl \
  --decisions data/review/<실제_검수_결정>.jsonl \
  --output data/review/<새_검수_ID>
```

검수자와 설명을 자동 생성해 승인을 대신하지 않는다. 판정은 해시에 연결되고 원래 주석과 검수 결정 해시를 보존한다. 아직 GUI에서 경계를 수정하는 기능은 없다.

## 채점 정의

겹친 예측 소유 구간은 합집합으로 만든다. 문맥 PCM의 추가 200ms는 검출 구간으로 중복 집계하지 않는다. collar/오차 허용 폭은 **0프레임**이다.

- 음성 누락 비율 = 알려진 음성 구간에서 검출하지 않은 프레임 / 알려진 음성 프레임.
- 비음성 오탐 비율 = 알려진 비음성 구간에서 검출한 프레임 / 알려진 비음성 프레임.
- `ignore` 프레임은 두 분모에서 제외하며 그 안의 검출량은 별도로 남긴다. 분모가 없으면 null이다.
- 목표 발화 전체 누락 = 선택한 발화 범위 안의 알려진 음성 프레임과 검출 소유 구간의 교집합이 0. 일부라도 겹쳤다고 단어 인식·발화 전체 보존에 성공한 것은 아니다.
- 잡음 WAV는 모두 `ignore`이므로 검출 유무/구간 수는 관측해도 **오탐률로 부르지 않는다**.
- 주석 상태별 결과를 섞어 정답 정확도로 보고하지 않는다. 예비 모드의 표에는 `gold=false`가 유지된다.

실제 endpoint 정답을 입력하려면 사람이 검수한 마지막 음성 끝이어야 하고 이후 WAV 끝까지 확실한 비음성 구간이어야 한다. 그 마지막 음성 구간과 겹치는 마지막 `vad_end` 이벤트에 대해서만 오디오 시간축 반환 지연과 경계 오차를 계산한다. `hard_limit`, EOF flush, 마지막 음성 누락은 지연 값이 null이다. 조기 종료의 음수 지연·경계 오차도 그대로 남긴다. **빠른 파일 실행의 wall 시간은 실시간 endpoint 지연으로 쓰지 않으며 wall 지연은 null**이다. 이번 예비 주석은 endpoint 정답을 모두 null로 둔다.

## RP5 실행과 재채점

Host에서 주석 생성 → 전체 테스트 → main commit/push → RP5 pull → 새 주석만 복사·해시 확인 순서다. 같은 SHA의 기존 WAV 패킷을 재사용한다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools prepare-annotations \
  --output data/review/<새_주석_ID>

# RP5. identity/rms26를 별도 폴더에서 순차 실행
.venv/bin/python scripts/edge_asr.py tools thermal-run --output results/preflight/<새_실행_ID>/identity -- \
  .venv/bin/python -u scripts/edge_asr.py tools probe-annotated-vad \
  --annotations data/review/<새_주석_ID>/annotations.jsonl \
  --output results/preflight/<새_실행_ID>/identity --policy identity --mode provisional
```

기본 모드 `reviewed`는 미검수/예비 라벨이 있으면 모델 로딩 전에 거부한다. 예비 진단을 하려면 `--mode provisional`을 명시한다. 53°C 이하 3회 시작 조건·75°C 중단선·현재 제한/새 이력/계측 결측 감시·600초/4GiB 외부 제한을 유지한다. 온도/클럭과 TID 관측 원시도 저장한다. 파일별 Python 오류나 native 활성 예산 초과는 실패 행으로 남기고 채점하지 않는다. 네이티브 중단·전체 실행 실패는 외부 기록으로 보존한다.

호출 RTF는 **전처리·VAD·구간 콜백의 호출 wall 시간 / 문맥을 포함한 WAV 길이**다. WAV 파일 읽기·원본 해시 검증·채점과 모델 로딩은 제외한다. 공통 PCM/구간 payload 해시 계산과 RSS 샘플러 오버헤드는 호출에 포함된다. 로딩/워밍업은 별도 기록한다. ASR batch RTF와 비교하지 않는다.

Host 회수 후 `summarize_annotated_vad.py --annotations ... --runs ... --output <새_JSON>`로 재채점한다. 소스·설정·음원/PCM 해시·주석·표본 누락/중복·열 통제·실패 수를 검증한다. 표는 양쪽에서 성공한 같은 표본만 사용하며 제외된 실패 ID/오류도 함께 보고한다. 실제 계측이 없는 항목은 0으로 채우지 않는다.
