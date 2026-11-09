# 고정100개 개발·회귀 평가 입력

2026-11-09. 입력 ID `edge100-20261109-01`, 선정 정책 `qwen-fixed100-v1`입니다. 모델 출력은 선정에 사용하지 않습니다. 이 문서는 준비 프로토콜이며 ASR 정확도·속도 결과는 별도 실측으로 남깁니다.

## 출처와 범위

| 자료 | 고정 출처 | 선택 수 |
| --- | --- | --- |
| Zeroth-Korean | [OpenSLR40](https://www.openslr.org/40/), 2018-03-20 공식 archive의 `test_data_01` | 식별 가능한 test 화자10명×6개 =60개 |
| FLEURS 한국어 | [Google 공식 배포본](https://huggingface.co/datasets/google/fleurs/tree/70bb2e84b976b7e960aa89f1c648e09c59f894dd/data/ko_kr), revision `70bb2e84b976b7e960aa89f1c648e09c59f894dd` | test40개 |

두 음성 자료의 라이선스는 CC BY4.0입니다. Zeroth의 코드 라이선스 Apache2.0과 음성 자료의 라이선스를 구분합니다. 공식 출처 안내·전사·화자 메타데이터를 원본과 함께 로컬에 보존합니다. 음원·정답·개별 발화 메타데이터는 Git에 올리지 않습니다.

두 자료는 낭독 중심입니다. 자유대화·잡음·마이크·30초 이상 연속 발화 조건은 이번100개에 포함하지 못한 범위입니다. FLEURS는 성별이 있으나 화자 ID가 없어 화자 수·화자 독립성을 확인하지 못합니다. 데이터가 모델 사전학습에 포함됐는지도 미확인입니다. AI Hub 자유발화의 접근·이용 조건이 확보되면 별도 버전으로 검토하고 이번 목록을 덮어쓰지 않습니다.

## 선정 규칙

- 원본 KSS50·FLEURS test100·추가 validation30의 manifest 해시를 고정하고, 이 자료와 겹치는 `ko_cer_v1` 정규화 정답과 기존 FLEURS 문장 ID를 제외합니다. 새100개 내부에서도 정규화 정답 중복을 제외합니다.
- seed는 `edge-asr-fixed100-20261109-v1`입니다. `SHA256(seed + '/' + source_id)` 순서로 할당량을 채우고 최종 manifest는 source ID 순으로 고정합니다. 저장소 과거 평가 결과를 읽지 않습니다.
- Zeroth는 각 화자에서 10초 미만5개·10~30초1개를 고릅니다. 알려진 화자는 남성4명·여성6명입니다.
- FLEURS는 남성25개·여성15개를 고릅니다. 길이별 할당은 남성 `[0,10)`초20개·`[10,15)`초4개·`[15,30)`초1개, 여성은 각각11·3·1개입니다. Zeroth와 합산하면 남성49개·여성51개입니다.
- 100개 전체의 총 음성 길이 목표는10~15분입니다. 할당량·길이 목표가 맞지 않으면 준비를 실패로 남기고 쉬운 입력으로 자동 교체하지 않습니다. 원음의 속도·gain·길이를 변경하거나 잘라내지 않습니다.

짧은 발화의 비중을 높인 작은 개발·회귀 목록입니다. 공개 코퍼스의 대표 무작위 표본이나 독립 holdout으로 해석하지 않습니다. 결과는 전체와 자료/길이 조건별로 보고하고 실패·빈 출력·반복 경고를 제외해 성능을 높이지 않습니다. 원본 전사는 그대로 사용하며 `ko_cer_v1` 채점만 공통 적용합니다.

## 원본 수집과 검증

FLEURS의 공식 `test.tsv`와 `audio/test.tar.gz`를 직접 받습니다. Dataset Viewer 개별 음성 API의 일시적503을 우회해 같은 고정 revision의 원본을 사용합니다. TSV의 정답은 0부터 세는3번 열이며 기존 Viewer `transcription`과 파일명으로 대응해 확인했습니다. TSV 행 번호와 Viewer 행 순서는 다르므로 서로 바꾸어 쓰지 않습니다. 전체 test archive는 공식 LFS SHA-256 `3489e529f2aad18d3357b746c5f955941d258b79dc60d8a102d5bced2a223184`로 검증합니다.

Zeroth는 전체10GB archive 대신 첫128MiB를 HTTP Range로 받습니다. ETag `"2684b9daa-567cdc82e7e00"`, 전체 크기10,339,720,618 bytes에 응답을 묶고 prefix SHA-256 `7cafae27a70315876bfea15406562e80a6f922d1fc437716a658020e64d97d41`을 고정합니다. 이 부분에 `AUDIO_INFO`, 완전한 test457개·전사와 다음 `train_data_01` 시작 헤더가 있습니다. **전체 archive의 공식 MD5는 부분 수신으로 검증하지 않았습니다.** 공식 HTTPS·ETag와 prefix/개별 원본 SHA 검증 범위를 명시적으로 구분합니다.

선정 원본60개 FLAC·40개 WAV를 보존하고 Host의 기존 ffmpeg로 16kHz mono PCM16 WAV를 만듭니다. 리샘플링·분할·무음 제거는 하지 않으며 PCM16 표준화 과정의 원본 정밀도 차이는 보존한 원본과 구분합니다. 프레임 수·형식·전체 PCM 해시·정규화 정답 중복·원음/변환본 해시를 확인합니다. Zeroth의 FLAC STREAMINFO 프레임 수는 공식 test457개 전체에서 기존 ffprobe와 대조합니다. ASR 모델은 로딩하지 않습니다.

## 준비·재검증

소스 캐시는 `data/sources/edge100-20261109-01/`, 최종 입력은 `data/evaluation/edge100-20261109-01/`입니다. 기존 경로가 있으면 해시를 검증하고, 불일치·불완전 자료는 덮어쓰지 않습니다. 중단된 `.part`나 `.preparing`은 새 실행 ID로 구분해 원인을 확인합니다. 기존 환경에 패키지를 설치하지 않습니다.

```bash
# Host: 공식 원본 수집·고정 선정·PCM 준비. 이미 받은 원본은 해시 확인 후 재사용.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  experiments/qwen3_optimization/prepare_fixed100.py --download

# Host/RP5: 네트워크·ffmpeg·모델 없이 저장된100개 검증
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  experiments/qwen3_optimization/prepare_fixed100.py --verify-only
```

최종 폴더에는 `manifest.jsonl`, `selection.json`, `statistics.json`, `provenance.json`, `original/`, `wav/`, 출처 문서·원본 전사 메타데이터가 있습니다. RP5에는 완성된 폴더를 새 경로로 복사한 뒤 같은 검증을 실행합니다. 실제 평가에는 이 manifest를 공통 `evaluate`에 전달하고 기존 시간·주소 공간·온도 기록 정책을 적용합니다. 다운로드·준비 완료만으로 CER·RTF·실시간 성능을 채우지 않습니다.
