# SenseVoiceSmall INT8 — M2-2 RP5 batch 검증

2026-10-28 Host SSH에서 RP5 기존 Python 3.11.18 / sherpa-onnx 2.0.1로 직접 실행했습니다. 소스 `3a8f320151efc7121352a290a8ce82f304f3c785`, 실행 당시 Git 변경 없음. 모델·패키지 변환/재설치 없이 공식 2024-07-17 INT8 배포를 새 모델 폴더에 추가했습니다.

## 같은 KSS 개발 자료 50개 결과

동일 WAV·정답·`ko_cer_v1`, 총 164.919초·정답 756문자. 모델별 발화당 1회이며 반복 통계나 독립 test가 아닙니다. **스레드 수·실행 시점·소스·외부 제한이 달라 통제된 속도/CPU 순위로 해석하지 않습니다.**

| 항목 | 기존 Zipformer | SenseVoiceSmall INT8 |
| --- | --- | --- |
| 스레드 설정 | 2 | 1 |
| 성공 / 계획 | 50 / 50 | 50 / 50 |
| Corpus CER | 11.6402% (88/756) | 5.1587% (39/756) |
| batch RTF | 0.108164 | 0.098176 |
| 추론 구간 평균 프로세스 CPU | 323.9777% | 100.0586% |
| 추론 구간 관측 최대 RSS | 246.750 MiB | 554.141 MiB |

SenseVoice 총 추론 16.191130초, 파일 처리 p50/p95 0.316991/0.504476초. 서로 다른 길이 파일의 batch 처리 분포이며 부분 자막/Refine 지연이 아닙니다. 워밍업 3/3은 집계 제외. 치환 25·삭제 9·삽입 5, 문자 커버리지 100%, 실패/미실행/중단 0.

## 로딩·런타임·모델 조건

- 모델 로딩 wall 1.832808초, CPU 2.066306초, 관측 최대 RSS 533.641 MiB. 추론 자원과 별도이며 RSS 차이로 모델만의 메모리를 추정하지 않습니다.
- sherpa 네이티브 ORT **1.28.2**, 별도 Python onnxruntime **1.31.1**. 두 버전을 대체하지 않았습니다.
- `language=ko`, `use_itn=false`, `model.int8.onnx`·`tokens.txt`, CPU 1스레드. 양자화 정밀도는 배포/설정 선언이며 알고리즘은 미검증/null입니다.
- raw/제어 태그 분리 필드를 저장했습니다. 이번 51개 실제 전사에는 sherpa 반환 제어 태그가 없었습니다.
- 로딩 전 체크섬 읽기로 cold-storage 성능은 아닙니다. RTF에서 로딩·워밍업·WAV I/O/변환·채점·쓰기 제외.
- 실행 전후 온도 52.35→61.70°C, governor ondemand, 두 시점 클럭 2.4GHz·throttled=0x0. 연속 피크 미계측.

## 외부 제한·짧은 입력 검사

단일 WAV 120초, KSS 600초 시간 예산과 TERM 후 5초 강제 종료 예산, `prlimit --as=4294967296 --core=0`을 사용했습니다. 실제 ASR PID의 `/proc/.../limits`를 읽어 4GiB soft/hard 적용을 확인했습니다. 가상 주소 공간 제한이며 RSS 상한이 아닙니다. RP5 커널에 memory cgroup 제어기가 없어 systemd 명령 성공(0)만으로 `MemoryMax` 적용을 주장할 수 없었습니다. 실제 scope의 `memory.max` 부재를 기록했습니다.

기존 공식 WAV 단일 샘플은 워밍업 3/3·본 측정 1/1 성공, CER 0%, 처리 0.346196초, RTF 약 0.0982였습니다. 단일 샘플 1회 결과입니다.

별도 프로세스에서 0.01/0.1/0.5/1초 합성 무음을 길이별 1회 전달하여 모두 정상 반환했습니다. **0.5초는 “응”, 1초는 “그”를 반환**했습니다. 0.01/0.1초는 빈 문자열이었습니다. 최소 입력 길이·실제 음성 정확도·일반 무음 오검출률의 검증이 아니며 padding/자동 재시도도 하지 않았습니다. 서비스·Refine 결합 전에 VAD/무음/endpoint 검증이 필요합니다. 이번 결과를 숨기는 단어 삭제는 적용하지 않았습니다.

## 보존·회수·검증

- RP5 기존 모델·데이터·실험/결과·`.venv` 파일 **2,028개 SHA-256 일치**, 설치 패키지 목록 일치. 새 모델 배포 파일 10개도 전후 해시 일치.
- 원시 기록 **37개 SHA-256 일치**, 별도 체크섬 목록 해시도 일치. 두 실행의 Host 재채점 전체 summary JSON 일치.
- 기존 Zipformer와 같은 50개 ID·정답·실제 WAV 해시 및 Host/RP5 소스·설정·모델 출처 해시를 대조했습니다.
- 원시 가설·음원·모델·archive·환경/진단·로컬 실행 스크립트는 Git 제외 경로에 보존합니다. Git에는 이 요약과 [작은 JSON](sensevoice-kss-dev50-20261028.json)만 추가합니다.

RP5 원시 루트: `/home/deepet/projects/edge-asr/results/executions/sensevoice-m2-2-eweeiec7`

Host 원시 루트: `results/executions/sensevoice-m2-2-eweeiec7`

이 단계는 **M2-2 batch 검증 완료**입니다. Streaming 첫 부분 자막·발화 종료 후 Offline Refine 확정 지연·VAD·마이크·동시 실행·최종 독립 평가·M2 전체 완료를 뜻하지 않습니다. Zipformer CPU 324%의 원인도 아직 미확정입니다. 다음은 같은 스레드/제한 조건의 비교와 무음 처리 검증이며 서비스 구조 선택은 이후 실측으로 결정합니다.

배포 출처: [sherpa SenseVoice 공식 안내](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html). 동봉 LICENSE는 [상위 FunASR 라이선스 안내](https://github.com/modelscope/FunASR#license)를 참조합니다. 코드 라이선스와 모델 가중치의 조건을 동일하게 간주하지 않았으며 모델/오디오는 재배포하지 않습니다.
