# 현재 상태 — Qwen3-ASR 최적화

2026-11-12 기준. 목표는 **RP5에서 Qwen3-ASR-0.6B의 한국어 정확도·완료율을 유지하면서 실제 인식 지연을 줄이는 것**입니다. [최적화 과정](research/optimization_history.md), [연구 계획](research/qwen3_optimization.md), [평가 조건](research/qwen_realtime.md), [최근100개 캐시 대조](../results/summaries/optimization/realtime-prefix-cache-20261112.md).

## 유지하는 연속 입력 기준

**Qwen 단독1배속 연속 입력**이 주 평가입니다. 다음 개발 권장은 **GGUF Q8_0·생성2T/음성·prefill3T·VAD 보정·ASR/VAD 판정5초·첫1초/이후4초·토큰 표시/접두부 재표시 보류·고정 text prefix KV**입니다. 개발에는 [configs/gguf_realtime.toml](../configs/gguf_realtime.toml)과 [대표 명령](guides/qwen3_optimization.md#현재-개발용-대표-명령)을 사용합니다. 기존 캐시 끔2/3 원본 `results/optimization/opt3/realtime-split100-20261111-01/configs/split-2-3.toml`은 보존하며, 기존 기본2T·모두3T 설정·모델·환경·bridge는 보존합니다. 실사용 채택은 보류합니다.

개발·검증 절차는 [CONTRIBUTING](../CONTRIBUTING.md), 직접 확인한 Host/RP5 버전과 환경별 용도는 [환경 기록](reference/environment.md)에 정리했습니다. 구조·안내 정리는 아래 실측의 재실행이나 성능 갱신을 뜻하지 않습니다.

## 최신100개 대조

소스 `e9656cc`·같은 새 prefix 빌드에서 **새off→on**을 비교했습니다. Zeroth60·FLEURS40·891.1448125초·정답3848자·추가 무음0초·파일 경계 초기화 없음입니다. Q8·2/3·VAD0.3/rms26-floor80·VAD/ASR5초·문맥0·첫1초/이후4초·토큰 표시/접두부 보류·유한 큐·워밍업·온도 대기 없는 운영 조건을 유지했습니다.

| 지표 | 고정 prefix KV off | on |
| --- | ---: | ---: |
| 연결 CER | 11.12% —428/3848 | 동일 |
| 최종 /부분 호출 | 210 /119 | 210 /125 |
| 실패 /입력 드롭 /자막 없는 원음 | 0 /0 /0 | 0 /0 /0 |
| 같은210개 최종 추론 합계 | 486.47초 | **442.40초** |
| 전체 추론 합계 | 623.42초 | **566.28초** |
| 첫 표시 p50 /p95 —100개 | 3.01 /4.41초 | **2.62 /4.13초** |
| 파일 끝 기준 최종 반환 p95 —100개 | 3.38초 | **2.82초** |
| 최종 호출 engine TTFT p95 —210개 | 1.826초 | **1.596초** |
| 관측 최대 RSS | 1833.81MiB | 1832.83MiB |
| 시작 /최고 온도 | 76.55 /87.55°C | 69.40 /84.80°C |

사전21조건을 통과했습니다. 최종210구간 PCM·전사·토큰 ID는 양쪽 및 이전2/3에서 같았습니다. 최종 합계9.06%·전체9.16%가 줄었고 on335호출은 모두 같은9토큰을 재사용했습니다. prefix 평가25.967→0초·KV 처리2.361→0.008초 외 다른 단계 비용도 달라 전체 개선을 prefix 생략만의 효과로 해석하지 않습니다. 모델 첫 추론은 cold miss, 워밍업3회 뒤 입력은 warm hit이며 콜드 시작과 구분합니다.

부분 호출이 늘어 문자 철회171→182자·수정48→50회, 자기 최종4자 유지 지연 p95 4.379→4.595초로 늘었습니다. 접두부 재표시 철회는 양쪽0이며 부분 정답 정확도는 미측정입니다. 모든 표시 품질이 개선된 것은 아닙니다.

첫 표시는 원음 시작→이벤트 발행이며 실제 GUI 시각이 아닙니다. 파일 끝 반환은 원음 입력 끝→마지막으로 겹치는 최종 결과 발행이고 음향 단어 끝과 다릅니다. 원음 경계 중첩1개로 발화별 corpus CER는null이며 전체 정답을 연결 CER에 포함합니다. 순차1쌍·다른 시작 온도·양쪽 열 제한을 포함한 운영 관측으로 통계적 유의성·실사용 채택과 구분합니다. [조건·단계별 비용·한계](../results/summaries/optimization/realtime-prefix-cache-20261112.md).

## 이전 판정과 구현·검증

- [모두2T/3T/4T 대조](../results/summaries/optimization/realtime-threads-20261111.md)는6개 선별 뒤100개 새2T→3T를 비교해 모두3T를 권장했습니다. 당시 최종 합계8.9%·전체8.3% 감소 기록은 보존합니다.
- [스레드 분리6개 반복](../results/summaries/optimization/realtime-split-20261111.md)은 기존 ABI 호환과3/3→2/3→2/3→3/3을 확인했습니다. decode 합계는 줄었지만 최종 처리 개선3.88%·2.82%로 반복3% 선별 조건에 미달해 그 실행은100개 없이 종료했습니다. 이번100개는 별도 추가 검증이며 이전 미통과 판정은 바꾸지 않습니다.3%는 실용 기준으로 통계적 유의수준이 아닙니다.
- 선택 항목 `num_threads_batch`를 명시하면 새6인자 ABI로 생성 스레드와 음성/prefill 스레드를 나눕니다. 생략하면 기존5인자 ABI·같은 스레드 수를 유지합니다. [구조](reference/architecture.md), [실행 안내](guides/qwen3_optimization.md).
- 최신 prefix 구현은 기존 어댑터·bridge·평가기 확장으로 처리했습니다. Host/RP5 각각 전체387개 중386통과·선택 ORT 헤더 검사1생략, 실제 GGUF 헤더/캐시 상태 검사·설정3개·새93파일/기존347파일·보호31항목·동일 부팅·독립 집계 일치를 확인했습니다. 단일 WAV 성공4회·의도적 callback 실패1회와 복구는 연속 지연 검사와 별도로 기록했습니다.
- engine TTFT는 native 호출 시작→첫 비EOG 토큰으로 음성 처리/prefill 포함·큐/Python 표시 제외입니다. 이번 최종210호출 p50/p95는off 1.243/1.826초→on 1.058/1.596초입니다. 고정 text prefix KV만 유지하며 음성 청크 KV 재사용·encoder 증분 실행은 미구현입니다.
- 현재100개는 낭독 개발/회귀 자료입니다. 자연 대화·잡음·독립 holdout·마이크·최종 장시간 운영·부분 정답 정확도는 미검증입니다. [데이터 출처·한계](research/fixed100.md). 이전 ONNX 재시작·화면/입력 멈춤 원인도 미확정으로 보존합니다.

## 음성·prefill3T/4T 선별 — 2026-11-12

같은6개에서2/3→2/4→2/3을 실행했습니다.3회 모두 최종15구간 PCM·전사·토큰 ID·50/280오류 동일·실패/드롭/미출력0입니다. 앞control 대비 후보의 음성/prefill4.65%·최종 합계0.51%·engine TTFT p95 7.84%는 줄었지만 전체 합계는0.069초(0.17%) 늘었습니다. 사전 선별 조건 하나 미달로 현재2/3을 유지하고100개는 실행하지 않았습니다. 같은2/3 앞뒤 최종 합계도4.44% 달랐으므로 효과0이나 통계적 악화로 해석하지 않습니다. [상세 결과](../results/summaries/optimization/realtime-prefill-20261112.md).

## 캐시 검토·구현의 중간 기록 — 2026-11-12

아래는 구현 직후까지의 중간 기록이며 최종 검증은 위100개 캐시 대조를 따릅니다.

현재2/3의 보존한100개·329호출을 Host/RP5에서 독립 재집계했습니다. 전체 추론634.318초 중 KV 초기화2.368초(0.37%)·음성/prefill347.376초(54.76%)·토큰 decode278.481초(43.90%)입니다. 같은 전체 PCM 재호출은0개, 부분→최종 반복 좌표는122.264초였습니다. 고정 코드상 특징/embedding은 음성이 늘 때 달라질 수 있어 단순 음성 prefix KV 재사용은 보류합니다. 분석 당시 음성 앞 고정 text prefix를 다음 작은 실험 후보로 정리했으며, 실제 prefix 시간·캐시 성능은 미측정입니다. 이 분석은 새 추론·런타임 변경 없이 수행했습니다. [분석·코드 근거](../results/summaries/optimization/realtime-cache-audit-20261112.md).

분석 이후 소스 커밋 `e9656cc`에서 `cache_text_prefix` 선택 구현과 앞/뒤 text·audio encoder·embedding 평가의 분리 계측을 추가했습니다. 기본은 끔이며 공유2/3 설정은 유지합니다. 실제 캐시 끔/켬의 전사 동등성·지연 효과는 검증 전입니다. [구현 구조](reference/architecture.md), [선택 실험 안내](guides/qwen3_optimization.md#고정-text-prefix-kv-선택-실험).

## 청취 도구·늦은 사례 진단 — 2026-11-12

[라이브·기록 청취 도구](guides/listener.md)를 구현했습니다. 개발 PC·노트북에서 SSH 포트 전달로 같은 RP5 실행을 보고 원음·정답·부분/확정 자막·지연·완료 CER를 확인합니다. RP5 실제6개65.7초·실패/드롭0과 개발 PC의 두 브라우저 동시 표시를 검증했습니다. 실제 노트북·물리 스피커 지연은 미측정입니다.

보존한 최신100개에서4자 표시가 늦은10개 모두 첫 부분 결과가1~3자였고,5초 구간의 최종 호출에서4자가 나왔습니다. 해당 최종 큐 대기는0.49~2.28ms로 작았습니다. 첫 후속 갱신 시점을 우선 검토할 근거이며 후보 변경은 아직 하지 않았습니다. [검증·진단 기록](../results/summaries/optimization/listener-20261112.md).

## 다음 작업

1. 청취 도구에서 늦은 사례를 확인하고, 첫 후속 갱신을 한 번 앞당기는 대조를 검토합니다. CER·수정량·최종 지연을 함께 판단합니다.
2. 2/3+고정 text prefix KV 구성을 새 개발 기준으로 유지하고 반복 대조·장시간 입력의 안정성을 확인합니다. 추가 최적화는 분리 계측한 audio encoder·embedding 평가·토큰 decode 비용과 콜드/웜을 근거로 검토합니다.
3. batch 대비 추가 오류134개와 부분 정답 정확도를 검토합니다. 자기 최종 가설 일치·문자 철회·음향 지연은 서로 구분합니다.
4. Q5/Q6/Q4는 기존 고유3개 선별 결과이며 최종 선정은 같은 연속 조건으로 판단합니다. 이후 독립 자료·마이크·장시간 운영을 검증합니다.

## 보존한 참조

[표시 안정성](../results/summaries/optimization/realtime-stability-20261111.md) · [토큰 전달](../results/summaries/optimization/realtime-token-20261111.md) · [갱신2초/4초](../results/summaries/optimization/realtime-schedule-20261111.md) · [VAD 보정](../results/summaries/optimization/realtime-accuracy-20261110.md) · [ONNX/GGUF 연속](../results/summaries/optimization/realtime100-backends-20261110.md) · [ONNX 출발·중단](../results/summaries/optimization/realtime100-onnx-20261110.md) · [batch 참조](../results/summaries/optimization/fixed100-baseline-20261109.md) · [3개 양자화](../results/summaries/optimization/realtime-controls-quant-20261110.md) · [갱신 간격 비교 영상](../results/summaries/optimization/realtime-video-20261111.md) · [전체 OPT 기록](../results/summaries/optimization/README.md).

이전 Zip+VAD+Refine 자막은 `legacy/`에 보존하며 현재 Qwen 단독 평가에 섞지 않습니다. [이전 연구](archive/model-comparison/README.md). 작업 순서는 **Host 개발·테스트 → main commit/push → RP5 동기화·실측**입니다.
