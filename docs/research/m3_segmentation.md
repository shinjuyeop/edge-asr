# M3-0 후속: 강제 구간 상한과 연속 입력

현재 VAD 입력 정책은 미승인이다. 이번에는 기존 누락/오탐을 보존하면서 **구간 상한·공통 PCM·유한 버퍼·독립 입력 계측**을 검증한다. ASR 동시 실행, 마이크, 텍스트 병합, 새 모델/패키지, OS 냉각 설정은 범위에 없다. 기존 2T 고부하의 냉각 기준 실패가 해결됐다고 간주하지 않는다.

## 사전 고정 정책

- 기존 Silero 파일 해시와 Python 환경 유지. VAD CPU 1T, 16kHz/512프레임, threshold 0.5, 최소 음성 0.1초, 종료 무음 0.3초, native soft limit **5초** 고정.
- 별도 소유 구간 상한 3/5/8초, 왼쪽 문맥 최대 200ms. Refine에 넘길 PCM 전체는 **상한+200ms** 이하다. VAD 종료와 강제 분할/EOF를 서로 다른 이유로 기록한다.
- 활성 VAD의 시작점과 이미 도착한 PCM을 사용해 **외부에서 소유 구간만 정확히 3/5/8초로 확정**한다. native `flush()`는 EOF에서만, `reset()`은 새 파일/세션에서만 호출한다. 512프레임 계산 때문에 경계보다 최대 511프레임(약 31.94ms) 뒤의 입력을 확인할 수 있으며, 100ms 수집 대기·큐·계산 시간이 별도로 더해진다. 실제 반환 시각을 기록하므로 정확한 벽시계 지연 상한을 주장하지 않는다.
- [sherpa v2.0.1 wrapper](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/voice-activity-detector.cc)의 flush는 누적 음성 버퍼를 비우지만 [Silero 신경망·히스테리시스 상태](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/silero-vad-model.cc)를 reset하지 않는다. **최초 RP5 실행에서 활성 flush 후 종료 무음 계산이 버퍼 시작 이전으로 돌아가 `Invalid n: -2752`가 발생했다.** `m3-segments-20261029-01/unpaced-identity`의 실패·1개 완료 행·native 로그를 보존한다. 새 구현은 이 API 사용을 제거하며 불량 구간을 무시하는 우회 처리는 하지 않는다.
- native 버퍼는 고정 **31초**, 활성 음성 처리 예산은 **30초**다. 다음 윈도가 예산을 초과하기 전에 `native_active_budget_exhausted`로 실패 중단한다. 현재 Python API가 내부 활성 PCM을 부분 제거하지 못하기 때문이다. **30초 이상 VAD 종료가 없는 연속 음성을 무기한 처리하는 서비스는 아직 지원하지 않는다.** 해당 한계는 외부 소유 구간 상한과 별개다.
- 공통 PCM 버퍼는 상한+문맥+1초. 각 100ms 전처리 블록을 512프레임으로 재조립하고 나머지는 유지한다. EOF의 마지막 VAD 윈도만 0으로 채우며 이 가상 패딩을 소유 구간에서 제외한다. 이미 덮인 PCM 요청·구간 상한 위반은 실패 처리한다. 자연 종료 시 native가 마지막 무음을 제외해 이미 확정한 소유 구간 끝보다 앞선 끝을 반환하면 구간을 취소하지 않고 `committed_tail_overhang_frames`로 여분을 기록한다.

## 입력과 의미

기존 `m3-vad-20261029-01/identity.jsonl`의 166개를 원형/공통 RMS 전처리에 동일 공급한다. **앞 0.5초·뒤 1초 무음도 전처리 상태에 포함**한다. 이전 검사의 사전 생성 RMS WAV는 파일 본문만 전처리했으므로 초기 RMS 이력 조건이 다르다. 원본과 과거 RMS 파일은 수정하지 않고, 새 목록에 Host에서 계산한 전체 PCM 해시를 고정한다. 두 ASR를 붙일 때 이 공통 PCM을 사용하고 구간별로 다시 정규화하지 않는다.

빠른 입력은 166개 × 3상한 × 2정책, 1배속은 기존 짧은 KSS 10개+무음 6개+잡음 6개에 연속 입력 1개와 약 30초 KSS 연결 입력 1개를 더한 **24개 × 2정책**, 5초 상한으로 고정한다. 새로운 최종 독립 평가가 아니며, 이번에는 threshold/음성 최소 길이를 튜닝하지 않는다.

`streaming.run_paced()`의 기존 독립 producer와 100ms/4칸 큐를 재사용한다. 소비자는 전처리·VAD·구간 처리만 수행한다. 큐 드롭이 하나라도 있으면 해당 실행을 실패로 남기며 압축된 시간축의 구간을 성공으로 채택하지 않는다. 큐/소비 이벤트의 drop·backlog와 producer CPU/RSS를 남긴다. WAV 전체는 재생 자료로 메모리에 있으므로 유한 처리 버퍼와 **전체 프로세스 메모리는 다른 범위**다.

모든 구간은 단조 프레임 번호, 소유 범위 `[start,end)`, 문맥 범위, 실제 반환 시각, PCM 해시를 가진다. 모든 소유 구간을 붙여도 VAD가 무음으로 판정한 입력은 포함하지 않는다. 소유 범위 중복 0은 단어 누락 0을 의미하지 않는다. 프레임 보존은 지속 음성 mock과 실제 payload 해시로 검사하고, 단어/음소 보존·마이크 초단문은 미검증으로 둔다. 실제 음성 끝 기준 지연·ASR 첫 부분 출력·Refine 확정 시간은 null이다. 자연 VAD 종료에 대해서만 WAV 끝 대리값을 계산한다.

## 실행·완료 기준

1. Host 전체 테스트, 지속 음성 mock의 조기 반환·상한·소유 프레임 연속·문맥 PCM 일치, 큐 과부하 실패 검사를 통과한다.
2. `prepare_segmentation_cases.py`로 새 목록/PCM 정답 해시를 만들고 main commit/push → RP5 clean main pull. 새 목록만 복사한다.
3. 보호 파일/패키지 스냅샷 후 기존 온도 감시(53°C 이하 시작, 75°C/현재 제한/새 이력/결측 중단)와 600초 timeout/4GiB 주소 공간 제한 안에서 아래 실행을 순서대로 수행한다. 각 출력 폴더는 새 ID를 사용한다.

```bash
# Host
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools prepare-segments
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
# RP5: identity/rms26를 순서대로; paced는 아래 추가 옵션 사용
.venv/bin/python scripts/edge_asr.py tools thermal-run --output <새_출력> -- \
  .venv/bin/python -u scripts/edge_asr.py tools probe-segments \
  --manifest data/evaluation/m3-segments-20261029-01/all.jsonl \
  --policy identity --output <새_출력>
# 1배속: manifest를 paced.jsonl로 바꾸고 --paced --max-segment 5
```

4. RP5 실제 완료·통제 조건·입력 해시·Host PCM 해시·구간 payload 해시·길이 상한·버퍼 상한·이벤트 누락·드롭 0을 검증한다. 실제 VAD 누락/잡음 오탐은 별도로 전부 보고하며 실패 사례를 삭제하지 않는다. 기계적 상한 합격과 음성 정책 채택을 구분한다.
5. 첫 실패는 `results/preflight/m3-segments-20261029-01`, 수정 후 검증은 새 `results/preflight/m3-segments-20261029-02`에 보존한다. 입력 목록은 동일한 `data/evaluation/m3-segments-20261029-01`을 사용한다. 작은 요약만 Git에 보존하며 기존 파일/설치 환경 해시를 재검사하고 Host/GitHub/RP5 main을 맞춘다.

다음에는 사람 검수 음성 경계와 자연 초단문/현장 잡음 개발 자료를 확보해야 한다. 실제 모델 동시 실행은 냉각·VAD 정책 합격 이후 별도 M3-1 단계에서 한다.

## 실측 결과와 추가 경계 검사

[2026-10-29 보고서](../../results/summaries/m3-segmentation-20261029.md)에 초기 실패와 수정 후 검증을 함께 기록했다. 기본 1배속 목록에서 강제 분할이 발생하지 않아 `boundary-plan.json`을 별도로 만들고, 빠른 입력에서 두 정책 모두 5초를 넘긴 `kss-train-01025-clean` 하나의 3/5초 경계를 각각 1배속으로 확인했다. 이 사후 선정은 코드 분기의 검증이며 정확도/성능 정책 선정이 아니다. native 설정·입력 전처리·원본을 바꾸지 않았으며 기본 48행과 별도 4행으로 보고한다.

```bash
# Host: 완료/통제/입력·구간 PCM/큐 이벤트를 검증한 뒤 기본 네 실행 집계
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools summarize-segments \
  --base results/preflight/m3-segments-20261029-02 --output <새_요약.json>
```

집계기는 기본 1,044행을 검사한다. 별도 경계 4행의 `verify_row()`·원시 이벤트 검증은 `boundary-analysis.json`에 기록했다. 강제 구간 끝 시각부터의 반환 시간과 자연 WAV 끝 대리 지연은 별도다. 두 값 모두 ASR 인식 지연이 아니다.
