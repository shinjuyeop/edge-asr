# 공통 파일 평가 도구

모델을 한 번 로드하고 여러 WAV를 순차 처리하여 CER·batch RTF·추론 구간 CPU/RSS를 기록합니다. 입력·채점·실행·저장은 공통이며 모델 호출은 `adapters/zipformer.py`, `adapters/sensevoice.py`로 분리했습니다. 저장된 가설은 모델·오디오 없이 재채점할 수 있습니다.

기본은 CPU Zipformer의 batch 평가입니다. 온라인 모델에 전체 PCM을 대기 없이 전달하고 파일마다 새 스트림을 생성·flush합니다. `configs/sensevoice_eval.toml`과 `configs/qwen3_eval.toml`은 각각 SenseVoiceSmall·Qwen3-ASR INT8 Offline batch 설정이며, `configs/zipformer_paced.toml`은 [1배속 스트리밍 모사](streaming_evaluation.md) 설정입니다. Qwen은 [별도 환경](setup.md)과 [확인한 자산·입력 예산](../research/qwen3_batch.md)이 필요합니다. 모델별 기존 측정은 [결과 색인](../../results/summaries/README.md), 현재 구현 상태는 [프로젝트 상태](../status.md)에서 확인합니다.

## RP5 시작

기존 모델·WAV·가상환경을 사용하는 한 프로젝트 폴더에서 실행하며 패키지 재설치는 필요하지 않습니다.

```bash
cd /home/deepet/projects/edge-asr &&
git pull --ff-only origin main &&
.venv/bin/python scripts/edge_asr.py check-config --evaluation configs/zipformer_eval.toml
```

`scripts/edge_asr.py`는 현재 `src`를 사용합니다. 설치한 환경에서는 `edge-asr evaluate`, `edge-asr score`도 같은 기능을 제공합니다.

## WAV와 정답 목록

[manifest 규격](../../benchmarks/manifest.md)에 따라 UTF-8 JSONL을 준비합니다. 필수 필드는 `id`, `audio_path`, `reference`이며 실제 파일 예시는 `configs/eval_manifest.example.jsonl`의 형식을 따릅니다. 입력은 설정과 일치하는 mono PCM16 WAV(기본 16kHz)이며 자동 resampling·채널 혼합은 하지 않습니다. 상대 경로 기준은 manifest 폴더가 아닌 CLI `--root`(기본 작업 디렉터리)입니다.

## 스피커 없이 공식 샘플 준비

기존 `0.wav`는 배포처의 [trans.txt](https://huggingface.co/k2-fsa/sherpa-onnx-streaming-zipformer-korean-2024-06-16/blob/ba6078bca4daf3f0dd37f79d0ab505af71df14a6/test_wavs/trans.txt)에 전사가 있습니다. 이를 사용하면 사용자가 직접 듣거나 ASR 출력에서 정답을 만들 필요가 없습니다. `tools prepare-official`은 로컬 WAV가 고정한 공식 파일과 SHA-256이 일치할 때만 전사·출처·예상 해시를 담은 목록을 준비합니다. WAV·모델은 수정하거나 다운로드하지 않습니다.

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

[공통 측정 계약](../reference/benchmark_design.md)은 batch와 Streaming/Refine 범위를 구분합니다. paced_stream은 [별도 계약](streaming_evaluation.md)에 따라 대기 제외 처리 시간과 대기 포함 replay·큐 지표를 분리합니다. 실행기 내부의 네이티브 충돌·OOM 강제 종료·하드 타임아웃 격리는 구현하지 않았습니다. RP5 실측 전 [외부 시간 제한·실패 기록 절차](../research/m2_sensevoice.md#외부-시간-제한과-실패-기록)를 적용합니다. 강제 종료에서는 마지막 `progress.jsonl`까지만 남고 manifest가 `running`일 수 있으며 이를 완료로 표시하지 않습니다.

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
