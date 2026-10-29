# 공통 파일 평가 도구

모델을 한 번 로드하고 여러 WAV를 순차 처리하여 CER·batch RTF·추론 구간 CPU/RSS를 기록합니다. 입력·채점·실행·저장은 공통이며 모델 호출은 `adapters/zipformer.py`, `adapters/sensevoice.py`로 분리했습니다. 저장된 가설은 모델·오디오 없이 재채점할 수 있습니다.

기본 모드는 **CPU Zipformer transducer의 batch 평가**입니다. 온라인 모델에 전체 PCM을 대기 없이 전달하고 파일마다 새 스트림을 생성·flush합니다. `configs/zipformer_paced.toml`을 지정하면 [공통 1배속 스트리밍 모사 평가](streaming_evaluation.md)로 청크별 가설·지연·드롭·백로그를 기록합니다. `configs/sensevoice_eval.toml`은 [SenseVoiceSmall INT8 Offline batch](../research/m2_sensevoice.md) 설정이며 Host mock과 RP5의 단일 WAV·KSS 50개 추론/재채점을 확인했습니다. Qwen·마이크는 미구현입니다. 2026-10-28 공식 WAV 1개에 대한 Zipformer RP5 실행 결과를 Host에서 SSH로 직접 읽고, 결과 파일의 체크섬과 Host 재채점 일치를 확인했습니다. 단일 샘플 1회 측정이며 여러 발화용 데이터 준비는 [데이터 안내](datasets.md)를 따릅니다.

후속 [KSS 개발용 50개 기준선](../../results/summaries/zipformer-kss-dev50-20261028.md)은 Host에서 SSH로 RP5에 직접 실행하여 50/50 성공을 확인했습니다. 단일 화자의 낭독 50개를 각각 1회 처리한 개발 자료이며 CER 11.64%, RTF 0.1082입니다. 다화자·잡음·공통 스트리밍은 이 결과로 검증하지 않았습니다.

[SenseVoice M2-2 기록](../../results/summaries/sensevoice-kss-dev50-20261028.md)은 같은 50개 WAV·정답·정규화에서 CER 5.16%, RTF 0.0982입니다. 1스레드 설정·별도 시점의 1회 측정이며 Zipformer 2스레드와 통제된 처리 성능 순위로 해석하지 않습니다. 합성 무음의 비어 있지 않은 전사도 관측했으므로 VAD·무음·실시간 Refine 적합성은 추가 검증합니다.

## RP5 시작

기존 모델·WAV·가상환경을 사용하는 한 프로젝트 폴더에서 실행하며 패키지 재설치는 필요하지 않습니다.

```bash
cd /home/deepet/projects/edge-asr &&
git pull --ff-only origin main &&
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/zipformer_eval.toml
```

`scripts/edge_asr.py`는 현재 `src`를 사용합니다. 설치한 환경에서는 `edge-asr evaluate`, `edge-asr score`도 같은 기능을 제공합니다.

## WAV와 정답 목록

UTF-8 JSONL 한 줄마다 `id`, `audio_path`, 사람이 확인한 `reference`를 기록합니다. `split`, `condition`은 선택이며 기본값은 `evaluation`, `unspecified`입니다. ID는 고유해야 합니다. 예시는 `configs/eval_manifest.example.jsonl`이며 실제 WAV는 포함하지 않았습니다.

```json
{"id":"sample-001","audio_path":"data/evaluation/001.wav","reference":"오늘 날씨가 좋습니다.","split":"evaluation","condition":"clean"}
```

현재 입력은 mono PCM16 WAV이며 샘플레이트는 설정과 일치해야 합니다(기본 16kHz). 자동 resampling·채널 혼합은 하지 않습니다. 경로는 manifest 폴더가 아니라 CLI `--root` 기준이며 기본값은 작업 디렉터리입니다. 절대 오디오 경로도 지원합니다.

## 스피커 없이 공식 샘플 준비

기존 `0.wav`는 배포처의 [trans.txt](https://huggingface.co/k2-fsa/sherpa-onnx-streaming-zipformer-korean-2024-06-16/blob/ba6078bca4daf3f0dd37f79d0ab505af71df14a6/test_wavs/trans.txt)에 전사가 있습니다. 이를 사용하면 사용자가 직접 듣거나 ASR 출력에서 정답을 만들 필요가 없습니다. `prepare_korean_test0.py`는 로컬 WAV가 고정한 공식 파일과 SHA-256이 일치할 때만 전사·출처·예상 해시를 담은 목록을 준비합니다. WAV·모델은 수정하거나 다운로드하지 않습니다.

```bash
.venv/bin/python scripts/edge_asr.py tools prepare-official &&
.venv/bin/python scripts/edge_asr.py evaluate \
  --manifest data/evaluation/official-test0.jsonl
```

준비 도구는 동일한 목록에는 재사용 가능하며 다른 내용의 기존 파일은 덮어쓰지 않습니다. 공식 WAV 해시가 다르면 자동으로 정답을 연결하지 않고 중단합니다. 평가 실행기도 선택 필드 `audio_sha256`를 검사하므로 준비 후 WAV가 바뀌어도 추론 전에 해당 파일을 실패로 처리합니다.

공식 파일은 56,424프레임·16kHz·3.5265초이며 SHA-256은 `0faf0b037efe428e5e561195f4d2aa148b2a0a2a5fc540b2c184b9d5c241e984`입니다. 전사·음원 출처는 배포 리비전 `ba6078bca4daf3f0dd37f79d0ab505af71df14a6`에 고정했습니다. Host에서 공식 파일·해시·헤더를 확인했으며 실제 RP5 파일과의 일치는 준비 도구를 RP5에서 실행할 때 검사합니다.

이 한 파일은 도구 동작 확인용이며 일반 한국어 정확도 결론에는 여러 발화·화자·잡음·길이가 필요합니다. 직접 준비하는 음성은 사람이 확인한 전사를 쓰고, 정답이 없는 경우 `reference`를 빈 문자열로 두어 추론·속도만 검사할 수도 있습니다. 이 경우 CER은 `null`이며 정확도를 측정했다고 표시하지 않습니다. 실제 WAV·정답 목록은 Git 제외 `data/`에 보관합니다.

목록의 모든 행을 실행하며 split 라벨로 자동 필터링하지 않습니다. tuning/evaluation을 함께 넣으면 전체 집계도 양쪽을 포함하므로 공식 비교에는 평가 전용 목록 또는 `by_split.evaluation` 집계를 사용합니다.

## 실행과 결과

```bash
.venv/bin/python scripts/edge_asr.py evaluate \
  --config configs/zipformer_eval.toml \
  --manifest data/evaluation/manifest.jsonl
```

기본값은 2스레드, 모델 로딩 한 번, 최초 유효 WAV로 워밍업 3회 후 각 파일 1회 측정입니다. 매 호출마다 새 인식 상태를 만듭니다. 모델 경로·tokens/encoder/decoder/joiner 파일명·스레드·flush용 무음 길이를 TOML에서 바꿉니다. 기본 무음은 0.66초이며 오디오 길이에는 더하지 않습니다. 기존 `zipformer_streaming.toml`·`ASR_THREADS`는 이 도구에 적용되지 않습니다.

새 결과는 `results/evaluations/eval-<UTC시각>-<임의문자>/`에 생성됩니다. `--output-dir`은 상위 경로만 바꾸며 기존 실행을 덮어쓰지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `manifest.json` | 상태·설정·입력 목록/모델/소스 해시·Git 커밋/변경 여부·모델 준비 시간/CPU/RSS·언어/ITN/양자화·워밍업 수·미계측 사유 |
| `environment.json` | Python·장비·설치 패키지 버전, sherpa 네이티브 ORT와 Python ORT 구분; pip 없는 uv 환경도 지원 |
| `predictions.jsonl` | 모든 계획 ID의 원본 정답/가설·상태·오디오 메타데이터·계측·실패 사유, 어댑터별 선택 raw/태그 결과 |
| `progress.jsonl` | 실행 중 완료한 파일의 기록. 파일마다 flush |
| `scores.jsonl` | 정규화 전후 정답/가설, 편집 수·CER·RTF |
| `summary.json`, `summary.md` | 전체와 split/condition별 집계·성공/실패/미실행·커버리지 |
| `error.txt` | 모델 준비 등 전체 실행 실패 시 traceback |

입력 목록의 오류·중복 ID는 모델 로딩 전에 거부합니다. WAV 누락·형식 오류·파일 추론 실패는 해당 파일을 기록하고 다음 파일을 계속 처리합니다. 워밍업 추론 실패는 남은 평가를 중단합니다. Ctrl-C에서는 완료 결과와 중단·미실행을 저장합니다. 종료 코드는 성공 0, 파일/워밍업 실패 1, 설정·목록·모델 준비 오류 2, 사용자 중단 130입니다. 요약 존재만으로 성공을 판단하지 말고 manifest와 성공/계획 수를 확인합니다.

아래 측정 계약은 batch 기준입니다. paced_stream은 [별도 계약](streaming_evaluation.md)에 따라 대기 제외 처리 시간과 대기 포함 replay·큐 지표를 분리합니다. 실행기 내부의 네이티브 충돌·OOM 강제 종료·하드 타임아웃 격리는 구현하지 않았습니다. RP5 실측 전 [외부 시간 제한·실패 기록 절차](../research/m2_sensevoice.md#외부-시간-제한과-실패-기록)를 적용합니다. 강제 종료에서는 마지막 `progress.jsonl`까지만 남고 manifest가 `running`일 수 있으며 이를 완료로 표시하지 않습니다.

## 측정 계약

- `ko_cer_v1`: Unicode NFC → 공백 제거 → Unicode 범주 P 문장부호 제거. 숫자·영문 대소문자는 유지합니다. `nfc_v1`은 NFC만 적용합니다. 코드포인트 단위 편집 거리를 사용하며 동률 경로는 대각선·삭제·삽입 순서입니다.
- Corpus CER은 성공한 비어 있지 않은 정답의 편집 수 합 / 정답 문자 수 합입니다. 파일 CER의 단순 평균이 아닙니다. 빈 정답 CER은 `null`이며 해당 샘플/비어 있지 않은 가설 수를 별도 집계합니다. 실패를 제외하므로 성공률·정답 문자 커버리지를 함께 해석합니다.
- Corpus RTF는 성공 파일의 추론 wall time 합 / 실제 오디오 길이 합입니다. WAV 읽기·해시·float 변환·모델 로딩·워밍업·채점·파일 쓰기를 제외하고 새 상태 생성·PCM 전달·특징 추출·디코딩·모델별 종료 처리·최종 전사 반환을 포함합니다. Zipformer의 기존 무음 flush는 유지하며 SenseVoice에는 적용하지 않습니다. 모델 준비 시간은 런타임 import·인식기 초기화 포함으로 별도 기록합니다. 기존 결과의 수치·정의는 바꾸지 않습니다.
- CPU는 추론 구간의 프로세스 CPU 시간 / wall time × 100이며 코어 하나가 100%입니다. 추론 중 RSS 계측 스레드 비용도 포함합니다. RSS는 모델 상주 상태의 주 PID를 100ms 간격과 호출 전후 경계에서 관측합니다. 정확한 피크·로딩 피크·자식 메모리를 보증하지 않으며 수집 불가는 `null`과 누락 수로 남깁니다.
- 파일 처리 시간 분포는 길이가 다른 WAV의 처리 시간 분포입니다. 부분 자막·실제 인식 지연·endpoint·드롭·백로그는 미계측 `null`이며 처리 시간으로 대체하지 않습니다. 기존 프로브의 측정 범위와 구분합니다.

M2부터 로딩 단계 주 PID CPU와 관측 RSS를 `model_load_cpu_s`, `model_load_resources`에 별도로 추가합니다. 추론 RSS는 계속 모델 상주 상태의 주 PID 값이며 모델 크기만의 메모리로 해석하지 않습니다. 미계측 사유와 모델별 언어·ITN·출력 정리 정책은 [M2 비교 계약](../research/m2_sensevoice.md#모델-간-비교-계약)을 따릅니다. KSS 50개는 개발·회귀 자료로 유지하고 최종 선정용 독립 데이터는 이후에 준비합니다.

## 재채점

평가 완료 후 출력된 실제 경로로 `RUN_DIR`을 바꿉니다.

```bash
RUN_DIR=results/evaluations/실제_출력된_실행폴더
.venv/bin/python scripts/edge_asr.py score \
  --predictions "$RUN_DIR/predictions.jsonl" --normalization ko_cer_v1
```

모델·WAV를 열거나 추론 라이브러리를 import하지 않고 `results/scores/score-.../`에 새 결과를 만듭니다. 기존 가설·처리 시간을 재집계하며 새 장비에서 속도를 재측정한 것이 아닙니다. 원래 실행 폴더는 수정하지 않습니다.

정답을 고쳤다면 `--references data/evaluation/corrected.jsonl`을 지정합니다. ID 집합이 원래 결과와 같아야 하며 reference만 교체합니다. 원래 split/condition·오디오·가설·시간은 유지합니다. 다른 오디오의 정답을 연결하지 않도록 ID를 유지하세요. 새 정규화 이름·교체 목록 해시는 재채점 출처에 기록합니다.

Unicode 데이터베이스 버전도 집계에 기록하며 재채점 출처에는 채점 Python과 채점기 소스 해시를 남깁니다. 서로 다른 정책·Unicode 버전의 CER을 같은 조건으로 취급하지 않습니다.

## 모델 확장

추상 기반 클래스·플러그인 시스템 없이 어댑터의 `model_files`, `load()`, `transcribe(Audio) -> str`, `close()` 경계를 사용합니다. 새 모델 모듈과 `adapters.create_adapter` 분기, 필요한 전용 설정 검증을 추가하면 데이터·채점·저장은 재사용합니다. 미구현 모델은 명시적으로 실패합니다.

Zipformer 호출은 보존 프로브와 [sherpa-onnx 공식 파일 디코딩 예제](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/online-decode-files.py)를 참고했습니다. 단일 공식 샘플의 RP5 실행과 결과 회수를 확인했으며, 모델·데이터·모드를 확장할 때는 해당 조건의 새 RP5 검증이 필요합니다.
