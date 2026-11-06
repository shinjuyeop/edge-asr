# 실측·검증 기록

각 기록의 데이터·스레드·워밍업·실행 조건과 미측정 항목을 함께 확인하세요. 과거 수치를 현재 성능으로 재해석하지 않습니다. [현재 상태와 다음 과제](../../docs/status.md).

| 기록 | 내용 |
| --- | --- |
| [Qwen VAD 구간·Zip 동시 실행](qwen3-realtime-20261106.md) | 13/13개 성공·입력/보정 드롭 0, KSS CER 3.70%·5개 악화; Qwen 2T 반환 p95 약 3.1~3.4초·별도 후보 선택, 기본 SenseVoice 유지 |
| [Qwen3-ASR RP5 KSS 세 모델 비교](qwen3-kss-dev50-20261106.md) | 별도 환경·9개 구성·450/450회 성공, Qwen CER 3.57%이나 SenseVoice 대비 7개 악화·처리 비용으로 실시간 채택 보류; 각 300개 테스트·5,647개 파일 보존 |
| [Qwen3-ASR 배포 경로 검토](qwen3-route-review-20261106.md) | sherpa 2.0.1 CPU·INT8 ONNX 선정, RP5 설치 API·ARM64 wheel 확인. 새 모델 로딩·전사·성능 실측 없음 |
| [WAV 자막 단계 구조 정리](refactor-20261103.md) | 실시간 공통 코드·문서 통합, Host/RP5 각 287개 테스트·기존 29개 재집계 일치·단일 WAV 실제 회귀·5,613개 파일 보존 |
| [WAV 실시간 자막 MVP](m36-wav-mvp-20261103.md) | 한 명령·원문/보정 분리 표시·RP5실제4회; KSS첫stdout p50 0.853초·드롭0, 자동 교체 보류 |
| [연속 Zip 후보의 제한 교체 재생](m35-guarded-splice-20261103.md) | 새 추론 없는 Host/RP5 각6정책·39산출물 일치; KSS CER11.64→8.33%이나5개 악화로 자동 교체 보류 |
| [연속 Zip 토큰/시각·VAD 진단](m34-continuous-token-20261103.md) | RP53/3회·연속 전사/CER 유지·3,119개 유효 스냅샷;34구조 후보/66보류, 실제 교체0 |
| [같은 VAD 구간의 실제 병합](m33-segment-merge-20261103.md) | RP53/3회·100구간·드롭0; 장문42/43구간 EOF 전 확정, 구간 Zip CER/첫 부분 회귀로 채택 보류 |
| [파일 ID 단위 병합 정책 재생](m32-merge-replay-20261103.md) | 새 추론 없는 Host/RP5 각27정책·117파일 일치; VAD Refine KSS8/50개 악화·장문 중간 교정 약118초 대기로 자동 교체 보류 |
| [실제 온라인 VAD + Streaming/Refine](m31-online-vad-20261103.md) | 본6/6회·드롭0; VAD0 첫 부분 KSS p50/p95 0.853/1.457초, 소유 끝→반환p95 KSS0.665초/합성0.605초 |
| [별도 디지털 무음 온라인 검사](m31-online-silence-20261103.md) | 별도3/3회; 고정4/6개 오출력 보존, VAD0/후속은 방출·Refine 호출0, 빈 정답 CER/미호출 지연 null |
| [시작 여유/강제 분할 문맥 분리](m3-context-split-20261103.md) | 12/12회·318개 성공; 시작200ms는 KSS1자 개선/합성12자 악화, 후속은 실제 적용1회에서1자 개선으로 후보 유지 |
| [Streaming + Refine 실제 동시 비교](m31-concurrent-20261103.md) | 단독/동시 10/10 성공·손실 0; 1+2 반환 p95 KSS 0.344초/합성 0.356초, 장문 Refine CER 18.30% 회귀로 자동 교체 보류 |
| [VAD 시작 여유·200ms 문맥 후보](m3-vad-context-20261103.md) | 159/159 성공; KSS 5.56→5.42%, 합성 장문 6.37→7.58%로 일괄 채택 보류, 실제 강제 방출 1회씩 |
| [자연 발화 구간 정책 CER 비교](m3-segmented-accuracy-20261102.md) | SenseVoice 1T, KSS 150/150 성공; 전체/고정5초 5.16%, VAD 5.56%, 시작/문맥 회귀 확인 |
| [기본 열 보호 아래 8분 운영 부하](m3-operational-20261102.md) | Zipformer 2T 1,203회 완료, 최고 85.9°C·실제 스로틀링 관측; 진단 batch RTF 0.1209, 통제/실시간 평가와 구분 |
| [75°C 경고/80°C 중단 구현·실측](m3-thermal80-20261102.md) | 새 정책 작동·196개 Host/RP5 테스트 확인; Zipformer 2T는 80.95°C에서 중단해 장시간 미통과 |
| [냉각 재검증·자연 발화 평가 준비](m3-cooling-recheck-20261102.md) | 기존 Zipformer 2T가 75°C 기준을 재차 미통과; 추가 ASR 중단, KSS 구간 CER 평가 입력·설계 준비 |
| [중첩 입력의 30초 초과 VAD 검증](m3-vad-overlap-rp5-20261102.md) | bounded 30초 초과 활성·유한 버퍼 검증, native 안전 중단 6건 보존, 원시 회수·보존 대조 완료 |
| [RP5 확률 VAD 비교·지속 상태 점검](m3-bounded-vad-rp5-20261102.md) | 실제 320개 비교·파생 입력 24개 완료; 30초 초과 활성 미입증, 기본 안전 제한 유지 |
| [지속 음성 경로 준비·냉각 재분석](m3-engineering-followup-20261029.md) | Host 모의 검증, 기존 팬/열 기록 재분석; RP5 새 실행은 연결 실패로 미완료 |
| [구조 정리 검증](refactor-20261029.md) | 도구/공통 코드/문서 재배치, Host·RP5 회귀와 기존 결과 보존 |
| [m2-3-comparison-20261028](m2-3-comparison-20261028.md) | M2-3 RP5 통제 비교·외부 평가 기록 (2026-10-28) |
| [m3-0-preflight-20261029](m3-0-preflight-20261029.md) | M3-0: 음량·냉각·VAD/구간 사전 검증 |
| [m3-public-vad-data-20261029](m3-public-vad-data-20261029.md) | M3 공개 VAD 자료 준비 — 2026-10-29 |
| [m3-segmentation-20261029](m3-segmentation-20261029.md) | M3-0 후속: 연속 입력과 강제 구간 상한 검증 |
| [m3-vad-followup-20261029](m3-vad-followup-20261029.md) | M3-0 후속: 실제 VAD·연속 전처리 검증 |
| [m3-vad-provisional-20261029](m3-vad-provisional-20261029.md) | M3 VAD 구간 주석·RP5 예비 비교 — 2026-10-29 |
| [sensevoice-kss-dev50-20261028](sensevoice-kss-dev50-20261028.md) | SenseVoiceSmall INT8 — M2-2 RP5 batch 검증 |
| [zipformer-kss-dev50-20261028](zipformer-kss-dev50-20261028.md) | Zipformer KSS 개발용 50개 발화 기준선 |
| [zipformer-paced-kss-dev50-20261028](zipformer-paced-kss-dev50-20261028.md) | Zipformer KSS 50개 발화 1배속 스트리밍 모사 기준선 |
| [zipformer-repeat-20261028-reported](zipformer-repeat-20261028-reported.md) | Zipformer 반복 측정: 2026-10-28 사용자 제공 요약 |
