# 평가 입력 manifest 설계

공통 파일 실행기는 UTF-8 JSONL(줄마다 객체 하나)을 사용합니다. 목록 파서·PCM16 WAV 검사·CER·재채점을 구현했습니다. 실제 오디오·사용자 정답 목록은 포함하지 않습니다. 준비 도구에는 출처를 고정한 공식 샘플 0의 전사만 포함합니다. [실행 안내](../docs/evaluation.md)를 따릅니다.

| 필드 | 의미 |
| --- | --- |
| `id` | corpus 안에서 유일한 샘플 식별자 |
| `audio_path` | 저장소 루트 기준 상대 경로 또는 RP5 로컬 절대 경로; 실행 시 기준 루트 기록 |
| `reference` | 정규화 전 한국어 정답 전사 |
| `audio_sha256` | 선택: 64자리 예상 WAV SHA-256. 불일치 시 추론 전 파일 실패로 처리 |
| `split` | 선택: tuning/evaluation 등 구분. 생략 시 evaluation |
| `condition` | 선택: clean/noisy 등 라벨. 생략 시 unspecified |

형식만 보여 주는 예시이며 실제 파일·전사가 아닙니다.

```json
{"id":"example-001","audio_path":"data/evaluation/example.wav","reference":"예시 문장입니다.","split":"evaluation","condition":"clean"}
```

오디오 SHA-256, 실제 길이·포맷, corpus 버전·권리·출처 및 manifest SHA-256을 실행 메타데이터에 보관합니다. 원본 길이는 WAV 프레임 수 / 원본 sample rate로 산출하며 resampling 이후도 확인합니다. 실제 인식 지연 측정에는 별도의 정답 구간 정렬(`t_ref_end`)이 필요합니다. 정렬이 없으면 해당 지표는 미계측입니다.

개인정보가 포함된 정답·경로는 Git에 넣지 않습니다. 작은 공개·합성 manifest를 추가하려면 데이터 라이선스와 실제 재현 가능성을 확인하고, 예시를 측정 데이터로 사용했다고 기록하지 않습니다.

현재는 설정 sample_rate_hz와 일치하는 mono PCM16 WAV를 요구합니다(기본 16kHz). 자동 변환은 하지 않습니다. 중복 ID·잘못된 필드는 모델 로딩 전에 거부합니다. 오디오 누락·형식 오류는 파일별 실패로 보존합니다. 빈 reference는 허용하지만 CER은 null이며 정답 전사를 대신하지 않습니다.
