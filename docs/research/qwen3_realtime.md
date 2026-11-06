# Qwen의 같은 VAD 구간·1배속 동시 실행 검증

2026-11-06 사전 계획. [전체 파일 batch](../../results/summaries/qwen3-kss-dev50-20261106.md)의 정확도 개선이 최대 5초 VAD 구간에서도 유지되는지, Zip과 함께 실행할 때 손실·반환 지연·메모리·발열이 어떤지 확인한다. 자동 교체는 계속 보류한다.

완료 결과는 [13개 비교·자막 후보 선택 검증](../../results/summaries/qwen3-realtime-20261106.md)에 기록했다. 아래는 결과를 보기 전에 고정한 절차를 보존한다.

## 환경과 입력

기존 `.venv`와 `.venv-qwen3-onnx-20261106-01`을 보존하고 RP5에 `.venv-qwen3-vad-20261106-01`을 별도로 만든다. Python 3.11.18·NumPy 2.4.6·sherpa/core 2.0.1·native ORT 1.28.2를 유지하고, VAD에는 기존과 같은 [Python ORT 1.31.1](https://pypi.org/project/onnxruntime/1.31.1/)을 설치한다. ARM64 wheel과 전이 의존성 8개의 실제 SHA-256으로 고정한 설치·freeze·pip check를 새 maintenance 경로에 기록한다. 기존 가중치는 수정하지 않는다.

- KSS 개발 50개: 기존 manifest·원음·expanded_script, 총 164.919초.
- 기존 합성 약 120.587초 1개: `data/evaluation/m23-stress/long-120.jsonl`. 이어 붙인 개발/지속 입력 스트레스 자료이며 자연 대화·독립 평가가 아니다.
- 두 자료의 CER와 지연을 합치지 않는다. 정답은 원본 발화 전체이며 구간 전사를 한 칸 공백으로 연결한다. `ko_cer_v1`·숫자/ITN 표기 정책을 유지한다. 빈 검출은 빈 가설, 누락한 Refine이 있으면 전체 Refine CER는 null이다.
- 기존 k2 Silero 가중치/해시, Python ORT 1T, threshold 0.5·min speech 100ms·min silence 300ms·긴 활성 threshold 0.9·silence 100ms를 유지한다. `vad0`·원음·최대 소유 5초·문맥 0이다.
- Qwen은 같은 6개 자산 해시·한국어·KV 512·출력 128·greedy·추가 텍스트 교정 없음이다. 잘림/반복 경고와 실패 원문을 보존한다.

## 결과 확인 전에 고정하는 순서

`tools compare-qwen3-realtime`은 새 출력 폴더만 허용한다. 입력/설정 해시와 아래 13개 실행을 `plan.json`에 먼저 기록한다.

| 단계 | 자료 | 순서 | 반복 |
| --- | --- | --- | --- |
| Offline 구간 비교 | KSS | SenseVoice 2T → Qwen 1T → Qwen 2T | 각 1회 |
| Offline 구간 비교 | 합성 120초 | Qwen 2T → Qwen 1T → SenseVoice 2T | 각 1회 |
| Zip 1T + VAD + Refine 동시 실행 | KSS | SenseVoice 2T → Qwen 1T → Qwen 2T | 각 1회 |
| 같은 동시 실행 | 합성 120초 | Qwen 2T → Qwen 1T → SenseVoice 2T | 각 1회 |
| 사전 지정 재확인 | KSS | Zip 1T + Qwen 2T + VAD | 추가 1회 |

Offline 6개 실행의 원본 정답/해시·VAD 소유/문맥 구간·PCM payload가 모두 같아야 온라인 단계로 넘어간다. 온라인 실행에서도 실제 방출 구간과 원본 PCM/float 해시를 대조한다. 같은 구간의 단독/동시 전사 차이를 기록하고 유리한 조건만 남기지 않는다. Qwen 2T KSS만 총 2회이며 이를 다른 구성이나 장시간 안정성의 반복 검증으로 일반화하지 않는다.

모든 실행은 새 프로세스, 모델 1회 로딩, 첫 원본 앞 최대 5초로 ASR/VAD 각각 3회 워밍업한다. 워밍업을 잘라도 채점 원본 전체와 실제 구간 입력은 유지한다. 기존 구간 평가기의 기본 워밍업 동작은 유지하고 이번 캠페인만 `--warmup-first-window`를 사용한다.

온라인은 독립 입력 프로세스의 실제 1배속·100ms·대기 5청크·drop_newest·10초 drain과 Refine 대기 2구간을 유지한다. 파일마다 Zip 연속 상태를 새로 만들며, VAD는 온라인으로 실행한다. 미리 잘라 놓은 구간을 1배속인 것처럼 표시하지 않는다.

## 계측과 실패 처리

운영 열 정책의 시작 53°C 이하·현재 제한 없음 3회, 기본 펌웨어 보호, timeout 600초, 프로세스별 가상 주소 공간 4GiB·core dump 0·실제 PID 제한 확인을 유지한다. 고정 클럭 통제 비교가 아니다. 온도/감속은 기록하며 저전압·계측 누락·입력 누락·native 실패·외부 제한 미확인은 캠페인을 중단한다. Refine 큐 포화만 발생했고 입력·추론·계측·프로세스 회수가 정상임을 확인한 경우에만 실패 원시를 보존하고 다음 고정 조건을 계속한다. 이를 성공으로 바꾸지 않는다.

Offline CER·개별 악화와 ASR/파이프라인 시간을 먼저 보고, 온라인에서는 다음을 측정한다.

- 오디오 시작→Zip 첫 부분 반환, 소유 구간 끝→VAD 방출/Refine 반환, 큐 대기·추론 호출·VAD 방출→반환.
- 입력 청크 및 Refine 구간 드롭, 큐 적체·종료 잔량. 손실은 실제 구간 길이로 집계한다.
- 동일 시각에서 관측한 Zip+VAD 주 PID와 Refine PID의 CPU/RSS 합, 입력 프로세스 자원, 온도/현재 제한. RSS 합에는 공유 페이지 중복이 있을 수 있다.
- 새 가중치나 출력 보정 없이 단독/동시 전사와 점수가 유지되는지, 기존 SenseVoice 대비 개선/악화 발화 수.

원본 한 개의 합성 자료에는 첫 부분 반환의 p50/p95를 성능 분포로 제시하지 않는다. Refine 구간별 분위수에는 관측 수를 붙인다. batch RTF와 온라인 반환 지연을 구분한다. 화면·SSH 도착·음향 정답 정렬·마이크 지연은 측정하지 않는다.

## 실행과 후속 판단

Host 모의/IPC·기존 결과 재집계 검사 → main commit/push → RP5 pull·같은 테스트 → 별도 실행 ID로 실제 측정 → Host 회수·재집계/해시·환경 보존 감사 순서다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-qwen3-vad-20261106-01/bin/python \
  scripts/edge_asr.py tools compare-qwen3-realtime \
  --output results/comparisons/qwen3-realtime-20261106-01
```

드롭·native 실패·원문/좌표 불일치가 없으면 반환 지연과 개별 악화를 바탕으로 Qwen을 별도 보정 후보로 사용할지 검토한다. 기존 SenseVoice 기본값과 자동 교체 보류는 유지한다. 결과가 허용하는 범위에서만 자막 선택 옵션을 추가하고 별도로 검증한다. 다화자·자연 잡음·장문·독립 평가 및 마이크·장시간 운용은 별도 후속이다.
