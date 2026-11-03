# 실측·검증 기록

각 기록의 데이터·스레드·워밍업·실행 조건과 미측정 항목을 함께 확인하세요. 과거 수치를 현재 성능으로 재해석하지 않습니다. [현재 상태와 다음 과제](../../docs/status.md).

| 기록 | 내용 |
| --- | --- |
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
