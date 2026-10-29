# M2-3 외부 자료 선택과 평가 범위

## 선택한 자료

[Google FLEURS 공식 배포](https://huggingface.co/datasets/google/fleurs)의 `ko_kr/test` 행 0~99를 선택했다. 리비전은 `70bb2e84b976b7e960aa89f1c648e09c59f894dd`, 정답은 배포처 `transcription`이다. 모델 출력을 보기 전에 연속 100개를 고정하며, 전체 test를 대표하는 무작위 표본이나 인증 평가 점수로 해석하지 않는다. 같은 문장의 다른 녹음이 포함될 수 있다.

저자: Alexis Conneau 외, **FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech** (2022), [논문](https://arxiv.org/abs/2205.12446). 라이선스는 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)이다. 배포 WAV를 Host ffmpeg로 mono PCM16 16kHz로 변환하며 원본·출처 카드·변환 버전·SHA-256을 보존한다. 음량 조정, 무음 제거, 정답 교정은 하지 않는다. 음원·전사는 로컬 `data/`에만 저장한다.

공식 카드에는 train 화자가 dev/test 화자와 다르다고 설명돼 있다. 하지만 제공 행에는 화자 ID가 없어 이번 100개에 포함된 **고유 화자 수·화자 균형은 미확인**이다. 제공 성별 라벨 분포와 고유 문장 ID 수를 보관한다. 성별 수를 화자 수로 바꾸지 않는다. 낭독 자료라 마이크/일상 대화/잡음 환경에 대한 일반화는 별도 확인해야 한다.

## 검토한 대안과 학습 중복

| 자료 | 출처·조건 | 이번 판단 |
| --- | --- | --- |
| FLEURS 한국어 test | Google 공식 배포, CC BY 4.0, 낭독, 화자 ID 미제공 | 작은 고정 외부 집합으로 추가 |
| Zeroth Korean test | [OpenSLR 40](https://www.openslr.org/40/), CC BY 4.0, test 457발화/10화자/1.2시간, 원 배포 묶음 약 10GB | 후속 확장 후보. 이번에는 다운로드하지 않음 |
| Seoul Corpus | [OpenSLR 113](https://www.openslr.org/113/), CC BY-NC 2.0, 자발화 | 사용 조건·정답 정렬을 더 검토한 후 별도 단계에서 준비 |

현재 Zipformer 모델 카드는 [KsponSpeech streaming 학습 recipe](https://github.com/k2-fsa/icefall/pull/1651)를 연결하며, 이 recipe는 KsponSpeech와 MUSAN 사용을 설명한다. 이는 FLEURS 비중복의 완전한 증명은 아니다. [SenseVoice 모델 카드](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)는 대규모 다국어 학습을 설명하지만 모든 학습 파일 목록을 공개하지 않는다. 두 모델 모두 이번 자료와의 **학습 중복 여부 미확인**으로 기록한다. KSS와 다른 출처인 외부 평가이며, 완전히 보지 않은 데이터라는 보장은 없다.

## 비교 정책

- KSS 결과로 설정을 고정한 `selection.json` 이후에 외부 ASR을 실행한다. 외부 CER을 보고 스레드/ITN/정규화를 바꾸지 않는다.
- 기존 `ko_cer_v1`을 그대로 사용한다. FLEURS 정답은 숫자가 숫자로 적힌 경우가 있고, SenseVoice는 기존 `ko`·ITN false를 유지한다. 숫자/영문 표기 차이도 점수에 포함되며 언어의 음향적 정확도만 분리한 수치가 아니다. 원문/가설을 숨기지 않고 필요 시 숫자 포함/미포함 발화를 보조 분석한다.
- 개발 KSS와 외부 FLEURS CER을 하나로 합쳐 순위를 만들지 않는다. 실패·미실행과 정답 문자 커버리지를 함께 보고한다. 100개·낭독·학습 중복 미확인의 범위에서만 결론을 낸다.

```bash
# Host: 새 Python 패키지 설치 없이 준비
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools prepare-external fleurs
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/edge_asr.py tools prepare-external stress
```

기존 자료가 있으면 출처 recipe와 모든 파일 해시를 검증해 재사용하며 덮어쓰지 않는다. 새 폴더만 RP5에 전송하고 전송 전후 해시를 비교한다. 실제 실행은 [M2-3 비교 절차](m2_comparison.md)를 따른다. 무음과 연결 합성 장문은 외부 정확도 자료와 별도로 보고한다.

길이별 스트레스 입력에 사용하는 KSS의 원저자는 Kyubyong Park, KSS Dataset(2018)이며 [고정 배포 카드](https://huggingface.co/datasets/Bingsu/KSS_Dataset/blob/48fdfd7ab1dbc1a62e4e8a8b9f4c360259d51d3c/README.md)의 CC BY-NC-SA 4.0 조건을 유지한다. 짧은 입력은 기존 완전한 발화 10개를 참조하고, 긴 입력은 기존 16kHz 변환본 사이에 0.3초 무음을 삽입한 연결물이다. 연결 순서·원본 ID·실제 길이를 provenance에 보관한다. 음성·전사·파생 음성은 공개 Git 저장소에 포함하지 않는다.
