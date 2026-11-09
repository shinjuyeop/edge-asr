# Qwen 최적화 기록

새 OPT 실험의 검토 요약을 이 폴더에 보관합니다. **OPT-0 기준선·오류 재현, OPT-1 ORT 호출 경계 계측, OPT-2 첫 GGUF Q8_0 비교를 완료했습니다. 사용자 TTFT·부분 자막·최종 장시간 검증은 후속입니다.** 기존 Qwen 비교 결과는 [이전 기록](../README.md)에 원래 파일명으로 남습니다.

새 보고서는 단계·고유 실행 ID, Host 모의 검사/RP5 실제 추론 구분, 소스 커밋·환경/모델/입력 해시, 정확도·완료율·계측 범위·실패·한계를 명시합니다. 미계측 값은 `null`과 사유로 기록합니다. 원시는 `results/optimization/<단계>/<실행ID>/`에 두고 이 폴더에는 작은 `.md`/`.json`/`.csv` 요약만 추가합니다.

[연구 계획](../../../docs/research/qwen3_optimization.md), [현재 상태](../../../docs/status.md), [구조 이행](../../../docs/reference/structure_migration.md).

- [2026-11-09 구조 개편 검증](restructure-20261109.md): Host/RP5 각 319개 테스트, 기존 29개 재집계 일치, 짧은 Qwen 실제 전사 회귀. OPT 기준선 측정과 구분합니다.

- [2026-11-09 OPT-0 첫 단독 기준선·56ms 재현](opt0-pilot-20261109.md): KSS 6개×5회 전사 회귀 0·RTF 0.6777, 짧은 잔여 구간의 반복 디코딩 확인. 사용자 지정 운영 조건과 초기 중단·취소를 함께 기록했습니다.

- [2026-11-09 OPT-1 ONNX 호출 경계 실측](opt1-profile-20261109.md): 고정 KSS에서 prefill+token decode 약71%, 56ms 반복의 후속64회 decode 비용 확인. 기존 패키지·모델 보존·계측 on/off 비교.

- [2026-11-09 OPT-2 GGUF Q8_0 CPU 비교](opt2-gguf-20261109.md): KSS50 양쪽50/50 성공. GGUF CER2.65%·RTF0.8025, ONNX CER3.57%·RTF0.7218. GGUF는 오류7개·RSS5.7% 감소, 처리11.2% 증가. 양쪽 열 제한 관측·속도 교체 보류.

- [2026-11-09 고정100개 입력 준비·검증](fixed100-preparation-20261109.md): Zeroth60·FLEURS40, 14분51.145초. 이전 정답/PCM 중복0, Host/RP5 220파일 해시 일치·각346개 테스트. ASR 성능은 미측정.
