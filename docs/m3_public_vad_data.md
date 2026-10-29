# M3 공개 자료 검수 준비

목적은 자연스러운 초단문·긴 발화·실제 잡음을 확보해 VAD 누락과 오탐을 검증할 기반을 만드는 것이다. **다운로드·파싱·WAV 준비 성공은 정답 검수나 VAD 성능 검증 성공이 아니다.** 기존 KSS/FLEURS 결과와 모델·환경은 유지한다.

## 출처와 사용 범위

- [Seoul Corpus, OpenSLR 113](https://www.openslr.org/113/): 시간 라벨 TextGrid와 FLAC. CC BY-NC 2.0이며 사용자는 2026-10-29 개인·학술 연구용이라고 확인했다. 논문 인용과 출처·변환 이력을 파생본에 남긴다. 상업용 사용으로 목적이 바뀌면 별도 조건 검토가 필요하다.
- [RIRS_NOISES, OpenSLR 28](https://www.openslr.org/28/): 전체 리소스 표기는 Apache 2.0이다. 이번에 받은 `pointsource_noises` 하위 자료의 LICENSE는 Freesound에서 Public Domain으로 표시된 녹음이라고 설명한다. 상위 표기만으로 다른 모든 하위 자료의 권리를 동일하다고 가정하지 않는다. 이번 WAV는 파일 ID 0001~0005를 추론 결과 확인 전에 고정해 받았고 대표성을 주장하지 않는다.
- Seoul Corpus 인용: Yun et al. (2015), *The Korean Corpus of Spontaneous Speech*, Journal of the Korean Society of Speech Sciences, 7(2), 103–109. 공식 배포처의 BibTeX를 사용한다.

배포 안내·아카이브·음원·전사·검수 자료는 `data/` 아래 로컬로 보관하며 Git에 넣지 않는다. 코드·준비 절차·작은 집계만 Git으로 관리한다. 이전 `acquisition.json`은 당시 음성 미다운로드 상태의 영수증으로 유지하고, 후속 실제 음성 확보는 별도 `audio-acquisition.json`에 기록한다. 로컬 SHA-256, gzip/ZIP 검사는 다운로드 후 무결성 근거이며 공개된 원본 체크섬과 대조했다는 의미는 아니다.

## 라벨 검사와 후보 선정

`src/edge_asr/textgrid.py`는 배포된 **full-text IntervalTier**만 읽는다. UTF-8/UTF-16, 따옴표·줄바꿈, 동일한 tier 이름을 지원하며 순서와 이름을 함께 보존한다. short/binary 형식이나 Point tier는 거부한다. [Praat 전체 규격](https://praat.org/manual/TextGrid_file_formats.html)을 모두 지원하는 범용 파서는 아니다.

구간 수·시간의 유한성·시간 범위·연속성·순서·파일 끝을 검사한다. 잘못된 TextGrid는 격리 목록에 이유를 남기며 조용히 고치지 않는다. 파일 이름과 CRC·해시를 확인하고 ZIP 경로를 파일 시스템에 무조건 추출하지 않는다.

선정 규칙 `public-vad-review-v1`:

1. 파일명과 `utt.ortho.` 구간 순서로 탐색한다. ASR/VAD 결과는 사용하지 않는다.
2. 발화 라벨 길이 80~500ms의 짧은 구간을 화자당 최대 2개, 8~20초의 긴 구간을 화자당 최대 1개 고른다. 길이는 **라벨상의 길이**이며 실제 음소 시작/끝의 검수 결과가 아니다.
3. 비어 있거나 특수 태그가 들어 있는 발화는 전사 후보로 채택하지 않는다. 특수 라벨은 전체 감사 목록과 후보 주변 태그에 보존한다. 이는 데이터 후보 선정 규칙이며 모델 출력을 바꾸는 필터가 아니다.
4. 원본 문맥을 최대 앞뒤 0.5초 포함한다. 다른 발화·면접자 음성·웃음이 함께 들어갈 수 있으므로 문맥 전체를 목표 발화의 정답으로 간주하지 않는다.
5. 이번 후보는 전부 `development_candidate`이다. 화자 전체가 후보에 포함되므로 이후 이들을 튜닝에 쓰면 같은 화자를 최종 독립 평가로 다시 사용하지 않는다. 최종 보류 화자는 평가 실행 전에 따로 지정하거나 다른 자료에서 확보한다. 모델 학습 데이터 중복은 미확인이다.

**`<IVER>`, `<VOCNOISE>`, `<LAUGH>`, `<UNKNOWN>`, `<PRIVATE.INFO>`를 무음으로 일괄 치환하지 않는다.** 자료 설명서의 non-speech 분류와 서비스가 탐지할 모든 말소리의 범위는 다를 수 있다. 면접자 발화는 특히 별도 확인이 필요하다.

## 재현 절차

Host에 확보한 안내/라벨 영수증과 파일이 있어야 한다. 준비 스크립트는 자동 다운로드·패키지 설치를 하지 않으며 기존 출력 폴더를 거부한다. 각 실행은 새 ID를 쓴다.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/prepare_public_vad_review.py \
  --output data/review/<새_후보_ID>

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/materialize_public_vad_review.py \
  --review data/review/<새_후보_ID> \
  --source-zip data/sources/seoul-corpus-20261029-01/sound-flac.zip \
  --output data/review/<새_음원_ID> \
  --use-purpose noncommercial-research
```

원본 FLAC은 `original/`에 보존한다. Host ffmpeg로 원본 전체를 mono PCM16/16kHz로 변환한 후 `round(seconds*16000)`의 `[start,end)` 샘플 범위를 자른다. 변환 버전·명령 정책·원본 해시·실제 절취 프레임·WAV 해시를 기록한다. 임시 전체 변환 WAV만 정리하고 원본은 남긴다. 절취 구간이 실제 음원을 벗어나면 중단하고 실패를 기록한다. 잡음은 바이트 그대로 복사한다.

`index.html`은 로컬 음성을 재생하는 검수 페이지다. 서버 업로드나 외부 요청이 없으며 **재생이 검수 완료를 뜻하지 않는다**. `review.jsonl`의 `reference`, `reference_speech_intervals`, `expected_speech`는 모두 `null`이다. 기존 ASR 평가기에 넣으면 모델 로딩 전에 거부된다. 임의로 빈 문자열을 채워 무음 평가에 사용하지 않는다.

## 정답 확정 후의 평가 순서

1. 검수자는 실제 음성과 파형을 보고 말소리·웃음·호흡·잡음·겹침·불명확 구간을 구분한다. 200ms 안팎 응답이 실재하는지, 앞뒤 문맥에 별도 음성이 있는지 확인한다.
2. 음성 시작/끝을 최종 WAV의 샘플 번호로 표기한다. 배포 라벨을 초기 후보로 쓸 수 있지만 VAD의 검출 결과를 정답으로 복사하지 않는다. 불명확 구간은 별도 제외/불확실 사유를 기록한다.
3. 검수자·버전·검수 시각·대상 WAV 해시·전사·구간 및 포함/제외 사유를 별도 검수 결정 파일에 남긴다. 아직 이 결정 파일의 내보내기/채점기는 구현하지 않았다.
4. 개발/보류 화자와 판정 기준을 고정한 뒤 공통 원음/RMS 정책을 같은 입력에 적용한다. 현재 목록은 기존 평가기용 정답 manifest가 아니다. 정답 형식 검증과 구간 기준 누락·오탐·endpoint 채점을 추가한 뒤 RP5에서 측정한다.
5. 기존 5/9 누락·4/6 잡음 오탐 회귀 목록도 유지한다. 새 자료 결과로 과거 수치를 덮어쓰지 않는다. 냉각 조건을 통과하기 전에는 ASR 동시 부하를 재개하지 않는다.

이 단계에서는 새 ASR/VAD 성능 수치, 실제 음소 종료 지연, Hybrid 동시 성능을 산출하지 않는다. Host의 형식·해시 검사를 RP5 추론 검증으로 보고하지 않는다.
