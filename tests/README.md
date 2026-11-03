# 호스트 검사

`test_segmented_accuracy.py`는 같은 PCM·5초 상한·문맥 0·원본 발화당 VAD reset·모델 1회 로딩/워밍업/종료·실패 보존·중복 전사 연결·빈 검출의 삭제 오류·모델/음원 없는 공통 scorer 재채점·경계 손상 거부를 검사한다. 실제 VAD 음성 판별이나 ARM64 ASR 정확도 검증은 아니다.

200ms 후보는 강제 분할 중 소유 범위 불변·최대 5.2초 payload·실제 과거 원음 바이트·지연된 첫 시작의 여유·빈 검출과 모델 없는 재채점을 검사한다. 문맥에 의한 전사 반복을 제거하지 않는다. 과거 실제 문맥 0 원시의 재채점 불변은 별도 감사로 확인한다.

`test_thermal_policies.py`는 과거 기본값·75/80°C 경계·경고 후 계속 실행·실제 PID 제한 확인·현재 제한/새 이력/계측 누락/종료 직후 온도 중단을 mock으로 검사한다. `test_cooling.py`는 과거 기록에 저장된 80°C 한계를 기본 75°C와 혼동하지 않는지도 검사한다. 실제 팬/ARM64 부하 검증은 아니다.

운영 정책은 감속 허용·저전압/계측 누락 거부·제한 미확인 실패와 `operational_valid`/`control_valid` 분리를 검사한다. 실제 클럭과 sysfs 주파수를 구분하고 조회 실패를 null로 남기는지도 검사한다. `test_load_probe.py`는 가짜 모델의 1회 로딩·워밍업 3회 분리·호출별 monotonic 시각을 검증한다. 이 테스트가 RP5 장시간 실행이나 실시간 지연을 검증하지는 않는다.

`test_vad_probability.py`는 실제 가중치 없이 ORT 생성 인자·h/c 상태 전달·오류·히스테리시스·131.072초 지속 확률·35초 PCM의 3/5/8초 소유권/문맥/유한 버퍼를 검사한다. 강제 구간마다 reset하지 않으며 기본 native 안전 제한을 유지한다. `test_cooling.py`는 가상 sysfs와 합성 과거 기록으로 다중 팬·결측·읽기 전용·소프트웨어 정지/현재 제한/과거 이력 구분을 검사한다. ARM64 모델 실행·냉각 재시험 통과를 뜻하지 않는다.

`test_vad_duration.py`는 파생 입력 진단기의 3/5/8초 소유 PCM/상한·세션 reset·native 예산 실패 보존·지원 백엔드 거부를 지속 확률 mock으로 검사한다. 실제 신경망의 지속 활성 성공 여부는 RP5 기록으로만 판정한다.

`test_vad_overlap.py`는 PCM16 순환 위상·산술 평균·경계값·재현 가능한 파생 WAV·원본/기존 출력 보존·보류 자료/해시 손상 거부를 합성 음원으로 검사한다. 실제 VAD 출력과 인식 정확도는 검사하지 않는다.

`test_vad_annotations.py`, `test_vad_annotation_summary.py`는 구간 합집합·불명확 구간 제외·검수자/해시 검증·음수 endpoint·EOF/강제 분할 제외, 보류 표본 배제, 실패 원시 보존과 양쪽 성공 표본 재채점을 검사합니다. 배포 라벨 비교를 사람이 검수한 정답 결과로 바꾸지 않으며, 실제 RP5 VAD 검증과 구분합니다.

`test_public_vad_review.py`, `test_public_vad_audio.py`는 합성 TextGrid의 인코딩·중복 tier·손상·경계, 모델 출력과 무관한 후보 선정, 원본 해시·경로·출력 보존과 null 정답의 평가 거부를 검사합니다. Host ffmpeg가 있으면 합성 44.1kHz 음원의 실제 변환·절취도 확인합니다. 공개 음원 다운로드·사람 청취·RP5 모델 추론을 대신하는 검사가 아닙니다.

`test_sensevoice.py`는 합성 NumPy/sherpa로 Offline factory 인자·언어/ITN·1회 로딩·발화별 새 상태·padding/온라인 호출 부재·본문/제어 태그/raw 분리·자산/입력 오류를 검사합니다. 공통 batch 평가·CER·로딩/추론 자원 분리·모델/음원 제거 후 재채점·CLI와 미지원 paced_stream 사전 거부도 검사합니다. 실제 SenseVoice 가중치나 추론 런타임은 사용하지 않습니다.

`test_runtime_diagnostics.py`는 sherpa 네이티브 ORT와 Python 패키지 버전 구분, 기본 조회의 import 부재, probe 실패/null·모델 미생성, Linux stat 파싱·TID CPU 차분·경계 누락·PID 종료/재사용을 검사합니다. 짧은 CLI 관측은 실행 중인 Host 테스트 PID만 읽으며 RP5에 접속하지 않습니다.

`test_streaming.py`는 독립 입력 프로세스와 실제 유한 큐를 사용해 1배속 입력·짧은 마지막 청크·부분 가설 부재·느린 추론의 실제 드롭·예외/Ctrl-C 자식 회수, 처리 중 청크를 제외한 백로그 재구성·격자 누락 피크, 모드 혼합·NaN 계측 거부를 검사합니다. 모델은 가짜 어댑터이며 독립 CLI의 spawn도 합성 런타임으로 검사합니다. 모델/WAV/events 제거 후 재채점도 확인합니다. 실제 RP5·ASR 검증과 구분합니다.

`test_kss_subset.py`는 다운로드를 모의하고 데이터 리비전·행·음원 URL 검증, 기존 자료 보존, 실패 시 미완성 목록 발행 방지, 손상 감지와 오프라인 재사용을 검사합니다. Host에 ffmpeg가 있으면 임시 합성 44.1kHz WAV의 실제 16kHz 변환도 검사하고, 없으면 변환 검사 2개만 건너뜁니다. 외부 KSS 음원을 테스트에서 다운로드하거나 ASR을 실행하지 않습니다.

Python 3.11 가상환경에서 기본 패키지 설치 후 저장소 루트에서 실행합니다.

```bash
python -m unittest discover -s tests -v
sha256sum -c experiments/m1_zipformer/SHA256SUMS
bash -n scripts/deploy_rp5.sh
```

설정 경계값과 CLI의 JSON 출력·실패 종료 코드, 합성 Summary 파싱·시간 반올림, 분위수·교차 순서, 모의 프로브의 성공·실패·시간 초과·자원 수집·워밍업 제외·기존 파일 보존을 검사합니다. 초기 원시 로그는 테스트 의존성이 아닙니다. 실제 모델 런타임·가중치·평가 WAV·RP5 연결은 요구하지 않습니다. WAV 포맷 검사는 임시 합성 WAV를 생성해 사용합니다. Linux 프로세스 검사는 다른 OS에서 건너뜁니다. 이 검사의 통과는 실제 추론·ARM64·실시간 서비스 성능 검증이 아닙니다.

`test_consolidate.py`는 임시 Git 저장소에서 한 폴더 통합 미리보기·실제 적용, 가상환경/모델·과거 결과 보존, 충돌 파일/Git 메타데이터 백업, 실패·중단 복원, 실행 중 실험·수정 소스·다른 Git 이력·symlink 거부와 hard link 보존을 검사합니다. Linux/Git CLI가 필요하며 실제 RP5 폴더를 사용하지 않습니다.

`test_evaluation_inputs.py`, `test_evaluation.py`는 JSONL·PCM16·정규화/CER·가중 집계·실패 커버리지, 모델 1회 로드·파일별 상태·워밍업 제외·중단 기록·기존 결과 보존, 모델/오디오 없는 재채점과 정답 ID 검사를 다룹니다. 실제 CLI에서도 가짜 NumPy/sherpa 모듈과 임시 합성 WAV로 실행·재채점을 확인합니다. 실제 ONNX 추론·정확도 검증은 아닙니다.
