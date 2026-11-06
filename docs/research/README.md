# 연구 계획·결정 근거

완료한 실험의 사전 조건과 재현 절차를 보존한다. 각 문서의 당시 다음 작업을 현재 할 일로 해석하지 않는다. 현재 채택 상태는 [프로젝트 상태](../status.md), 실제 수치/원시 해시는 [검증 기록](../../results/summaries/README.md), 현재 자막 사용법은 [WAV MVP](../guides/live_captions.md)에 있다.

## M2 모델·비교 계약

- [Qwen VAD 구간·Zip 동시 실행 검증](qwen3_realtime.md) — 같은 구간의 단독/온라인 비교·지연·손실·자원 사전 계획

- [Qwen 첫 실제 실행·공통 batch 비교 계약](qwen3_batch.md) — 별도 환경 smoke·같은 KSS·세 모델·1/2/4스레드 탐색 비교 완료, 결과 연결
- [Qwen3-ASR-0.6B RP5 CPU 배포 경로 검토](qwen3_deployment.md) — 2026-11-06 모델 수급 전 sherpa INT8 ONNX 선정·설치 API 확인 기록
- [M2-0 / M2-1 / M2-2: SenseVoiceSmall INT8 batch 통합·실측](m2_sensevoice.md)
- [M2-3 통제 모델 비교](m2_comparison.md)
- [M2-3 외부 자료 선택과 평가 범위](m2_external_data.md)

## M3 입력·VAD·열 조건

- [M3-0: 실시간 Hybrid 전제조건](m3_preflight.md)
- [M3-0 후속: VAD 실제 검증과 연속 전처리](m3_vad_followup.md)
- [M3-0 후속: 강제 구간 상한과 연속 입력](m3_segmentation.md)
- [M3 공개 자료 검수 준비](m3_public_vad_data.md)
- [M3 VAD 구간 주석과 예비 비교](m3_vad_annotations.md)
- [M3 후속: 지속 음성 VAD와 냉각 진단](m3_bounded_vad.md)
- [M3 열 정책: 통제 비교와 실제 운영 검사](m3_thermal_policy.md)

## 구간 정확도와 문맥 후보

- [M3 다음 평가: 자연 발화의 구간 처리와 CER](m3_segmentation_accuracy.md)
- [M3 VAD 시작 여유·200ms 왼쪽 문맥 비교](m3_vad_context.md)
- [M3-1 후속: 음성 시작 여유와 강제 분할 문맥 분리](m3_context_split.md)

## 동시 실행과 병합 후보

- [새 문장 평가와 30분 연속 입력](file_validation.md) — 문장 중복 제외·별도 워밍업·고정된 6개 조건의 사전 계획
- [M3-1 Streaming + Refine 동시 부하 측정](m31_concurrent.md)
- [M3-1 온라인 VAD와 Streaming/Refine 동시 계측](m31_online_vad.md)
- [M3-2 파일 ID 단위 선택 정책과 저장 실측 로그 재생](m32_merge_replay.md)
- [M3-3 같은 VAD 구간의 Streaming/Refine 병합 실험](m33_segment_merge.md)
- [M3-4 연속 Zip의 native 토큰 시각과 VAD 경계 진단](m34_continuous_tokens.md)
- [M3-5 연속 Zip 구조 후보의 제한 교체 재생](m35_guarded_splice.md)

새 실험은 기존 정책/결과를 고치지 않고 별도 조건·실행 ID로 기록한다. 단순 사용법과 최신 상태를 이 문서들에 반복 추가하지 않는다.
