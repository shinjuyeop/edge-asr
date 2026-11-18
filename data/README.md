# 로컬 오디오·정답

현재 Qwen 최적화의 고정 입력은 `evaluation/edge100-20261109-01/manifest.jsonl`입니다. Zeroth60개·FLEURS40개, 총891.1448125초이며 원본·변환 WAV·정답·해시를 함께 보존합니다. [출처·선정·준비/재검증](../docs/research/fixed100.md), [평가 기준](../docs/research/fixed100_baseline.md), [실제 ASR 결과](../results/summaries/optimization/fixed100-baseline-20261109.md)를 확인하세요.

[eval_manifest.example.jsonl](../configs/eval_manifest.example.jsonl)은 형식 예시이며 실제 평가 목록은 아닙니다. [manifest 명세](../benchmarks/manifest.md), [정답 정규화·계측 계약](../docs/reference/benchmark_design.md)을 따릅니다. 모델 비교의 실행 순서는 원본 목록을 바꾸지 않고 새 결과 경로에 준비합니다.

보완 입력은 `evaluation/longform-20261117-01/`의 약1분 자연발화8개·낭독 연결4개입니다. `manifest.jsonl`은 전체, `natural.jsonl`·`reading.jsonl`은 조건별 목록입니다. 배포처 전사를 사용한 개발 세트이며 독립 청취 검수는 아직 하지 않았으며, RP5 실측은 [긴 음원 대조](../results/summaries/optimization/longform-stream-20261118.md)에 기록했습니다. [선정·재검증·정답 한계](../docs/research/longform.md)를 따릅니다.

기존 KSS50·FLEURS 자료는 같은 위치에 보존합니다. [데이터 안내](../docs/guides/datasets.md)에 이전 준비·이용 조건이 있습니다. Seoul Corpus 시간 라벨·원음·공개 잡음의 기존 `sources/`, `review/` 확보 기록은 [이전 M3 검수 계획](../docs/archive/model-comparison/m3_public_vad_data.md)에 있습니다. 기존 VAD 검수 후보는 긴 발화 세트와 별개이며, 미검수 발화 시각을 확정 음향 정답으로 바로 사용하지 않습니다.

이 README 외 음원·원본 전사·검수 페이지·캐시는 Git에서 제외됩니다. 새 자료는 별도 경로로 준비하고 기존 원본·manifest·실험 결과를 덮어쓰지 않습니다.
