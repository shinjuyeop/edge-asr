# M1: 기존 RP5 Zipformer 실험 보존

## 출처와 보존

`streaming_probe.py`와 초기 스레드별 1회 결과는 기존 RP5에서 가져온 자료입니다. 현재 main에는 반복 도구가 사용하는 원본 프로브와 작은 [수치 요약](summary.json)만 유지합니다. 원시 로그 3개는 [보관 태그](https://github.com/shinjuyeop/edge-asr/tree/archive-initial-zipformer-20261028/experiments/m1_zipformer/results) `archive-initial-zipformer-20261028`에 보존했습니다. 초기 원본을 잃지 않으면서 현재 작업 폴더에서 원시 결과 추적 예외를 없애기 위한 정리입니다.

현재 [SHA256SUMS](SHA256SUMS)는 원본 프로브만 검사합니다. 로그까지 포함한 기존 4개 해시 목록과 원본 바이트는 보관 태그에 남아 있습니다. 루트 `.gitattributes`는 현재 프로브의 줄바꿈 변환을 비활성화하며 보관 태그에는 로그의 줄바꿈 보존 설정도 함께 있습니다.

```bash
# 현재 작업 폴더의 원본 프로브 확인
sha256sum -c experiments/m1_zipformer/SHA256SUMS

# 필요할 때만 과거 원시 로그 확인. 작업 브랜치를 전환하지 않음.
git show archive-initial-zipformer-20261028:experiments/m1_zipformer/results/streaming_threads_2.txt
```

로컬에 보관 태그가 없다면 `git fetch origin tag archive-initial-zipformer-20261028`로 가져올 수 있습니다. 현재 브랜치를 과거 상태로 되돌릴 필요는 없습니다.

하드웨어 대상은 Raspberry Pi 5 ARM64 / Debian 13 / 8GB RAM이며, 기존 측정 당시 Python·패키지 정확한 버전, 온도·클럭·전원·냉각·백그라운드 부하는 원시 로그에 없습니다. 측정일도 미상입니다. 새 의존성 후보를 과거 측정 환경으로 간주하지 않습니다.

## 단일 샘플 결과

**모든 수치는 동일한 3.526초 WAV 단일 샘플에 대해 각 설정에서 1회 측정한 값입니다.** 결과의 우열이나 일반적인 실시간 성능을 결론 내릴 수 없습니다. 반올림한 표이며 원시 정밀도는 보관 태그의 `.txt`와 [summary.json](summary.json)을 참고하세요.

| 모드 | 스레드 | 오디오 시작 기준 첫 부분 출력(초) | 전체 경과(초) | 최종 처리 지연(초) | 최대 처리 마감 초과(ms) | RTF |
| --- | --- | --- | --- | --- | --- | --- |
| 파일 일괄 처리 | 미상 | 미계측 | 미계측 | 미계측 | 미계측 | 0.110 |
| 오디오 속도 모사 스트리밍 | 1 | 0.839 | 3.606 | 0.080 | 42.2 | 미계측 |
| 오디오 속도 모사 스트리밍 | 2 | 0.837 | 3.590 | 0.064 | 37.5 | 미계측 |
| 오디오 속도 모사 스트리밍 | 4 | 0.846 | 3.619 | 0.092 | 45.9 | 미계측 |

일괄 처리의 3.526초·RTF 0.110은 사용자 제공 기존 측정값입니다. 대응 원시 로그·실행 코드·스레드 설정은 없으므로 스트리밍 로그에서 계산한 값으로 표시하지 않습니다.

## 프로브가 측정한 것

- `models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/test_wavs/0.wav`를 사용합니다. 모델·WAV 자체는 이 저장소에 포함하지 않습니다.
- PCM16 mono 16kHz WAV를 전부 메모리에 읽고 100ms(1,600 샘플) 청크를 오디오 속도에 맞춰 전달합니다. 모델 로딩·WAV 읽기는 타이머 시작 전입니다.
- 최초 부분 출력 시점은 시작부터 첫 비어 있지 않은 가설까지의 경과 근사값입니다. 기록용 `now`는 디코딩 직후이고 결과 조회·표준 출력보다 앞이므로 실제 콘솔 표시 시각과도 차이가 있습니다. 입력 대기를 포함하며 음향적으로 정렬한 실제 인식 지연이 아닙니다.
- 끝에 10,560 샘플(0.660초)의 합성 무음을 추가해 빠르게 처리한 뒤 `input_finished()`를 호출합니다. 합성 무음 길이를 오디오 길이에 더하지 않으며 0.660초 동안 실시간 대기하지 않습니다.
- 최종 처리 지연은 전체 경과 시간에서 실제 WAV 길이를 뺀 값입니다. 합성 무음 처리·flush를 포함하며 실제 발화 끝 검출 지연을 입증하지 않습니다.
- 최대 마감 초과는 각 청크의 예상 입력 시각 대비 디코딩 직후의 양수 지연 최대입니다. 가설 조회·출력 비용 전체를 포함하지 않고, 큐 길이나 오디오 드롭을 측정하지 않습니다.

정답 전사·CER, 실제 인식 지연, p50/p95, CPU·RSS, 드롭·백로그는 미계측입니다. 스트리밍 전체 시간에는 sleep이 포함되므로 `전체 경과 / 오디오 길이`를 파일 일괄 처리 RTF와 비교하지 않습니다. 세 실행은 독립된 세 오디오 샘플이 아닙니다.

## 새 RP5 실행 방법 (미검증 절차)

[환경 설치](../../docs/setup.md)의 새 RP5 가상환경에 `.[zipformer]`를 설치하고, 저장소 루트에서 기존 자산 경로를 새 `models/` 링크로 연결하세요. 원본이 요구하는 파일은 다음과 같습니다.

```text
models/sherpa-onnx-streaming-zipformer-korean-2024-06-16/
├── tokens.txt
├── encoder-epoch-99-avg-1.int8.onnx
├── decoder-epoch-99-avg-1.onnx
├── joiner-epoch-99-avg-1.int8.onnx
└── test_wavs/0.wav
```

RP5의 저장소 루트, 새 가상환경에서 Git 제외 경로에 충돌하지 않는 새 실행 폴더를 사용합니다.

```bash
mkdir -p experiments/m1_zipformer/runs
run_dir=$(mktemp -d experiments/m1_zipformer/runs/probe.XXXXXX)
edge-asr env > "$run_dir/environment.json"
python -m pip freeze --all > "$run_dir/pip-freeze.txt"
for threads in 1 2 4; do
    ASR_THREADS="$threads" python experiments/m1_zipformer/streaming_probe.py \
        > "$run_dir/streaming_threads_${threads}.txt" 2>&1 || { echo "실패 로그: $run_dir" >&2; exit 1; }
done
```

이 명령도 설정별 단일 실행입니다. 워밍업·반복·지표 수집은 자동 제공하지 않습니다. 실패 로그를 확인하고, 다음 단계에서는 [공통 설계](../../docs/benchmark_design.md)에 따라 별도 실행기를 작성합니다. 새 원시 결과는 Git 제외 `runs/`에 기록합니다.

단일 실행을 이미 완료했다면 [반복 실행 도구](../../docs/rp5_repeat_benchmark.md)로 넘어갑니다. 원본 프로브를 별도 자식으로 실행하여 워밍업·교차 반복·요약·프로세스 자원을 기록하며 이 디렉터리의 원본 파일은 수정하지 않습니다.
