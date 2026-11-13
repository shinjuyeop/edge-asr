# Q4 연산별 프로파일링 — RP5

2026-11-13. **첫 token을 늦추는 audio/prefill을 node 단위로 좁혔습니다.** Q4 인코더의 계측 node 시간 중약50%는 유지한 FP16 합성곱·투영 경로였고, LLM prefill은 Q4_K/Q6_K 행렬곱이81.5~90.1%였습니다. 기존/계측 끔/켬의 전사·token ID는 모델별 원래 연속 호출과64회 모두 같습니다. [집계·해시·연산 순위](operator-profile-20261113.json), [사전 조건](../../../docs/research/qwen_realtime.md#q4-연산별-프로파일링--2026-11-13-사전-조건).

## 범위와 구현

소스 `8e5b6502e1446cac758fbc2e87c120ae56bb18f4`에서 기존 연속 실행의 첫 부분1.028초와 최종4.024초 PCM을 추출했습니다. 원음에 `dataset.prepare_track`을 적용한 뒤 기존 좌표로 잘라, 두 payload float32 SHA가 원래 호출과 일치하는지 확인했습니다. 잘린 원음의 정답을 임의로 만들지 않았으며 CER은null입니다. 이번은 **연속 평가에서 사용한 두 호출의 내부 진단**이고 새 연속 입력·마이크 평가가 아닙니다.

같은 두 PCM을8라운드 반복한16행 reproduction manifest와 기존 `qwen/benchmark.py`를 사용했습니다.1~4라운드는 준비/워밍업,5~8라운드를 집계합니다. 기존 Q8→계측 Q8→계측 Q4→기존 Q4 순서이며 계측 빌드의5~8라운드는 끔/켬/켬/끔입니다. 입력 순서는 short/long으로 고정했고 생성2T·음성/prefill3T·context512·출력128·고정 text prefix KV·greedy를 유지했습니다. 기존 빌드는 PCM별4회, 계측 켬/끔은각2회의 분석값이며 고유 음원이 많아진 평가가 아닙니다.

기존 engine6개·llama.cpp commit `7d7f0287530bc8981690f361d8c1627dbae1c99a`를 그대로 연결하고 **bridge만 별도 빌드**했습니다. 새 경로는 `runtimes/qwen3-opt/operators-20261113-01/bridge/`, SHA는 `ee0f1cb08dae7033f2068a8ab817d1c07e49ec95c86ef02a351f2a598c5132c3`입니다. 새 패키지 설치·엔진/모델 교체는 없으며 기존 prefix bridge와 공유 실행 설정은 보존합니다.

공개 LLM/mtmd `cb_eval`을 사용하는 선택 빌드 `--profile-operators`와 Python `set_operator_profile(bool)`을 추가했습니다. 단계·op·tensor 이름·좌우 입력 dtype/shape·횟수·시간을 집계하고 shape 변화·콜백 불일치·용량 초과를 드러냅니다. tensor payload를 읽거나 출력하지 않습니다. 한도는8192그룹이며 모든 실제 trace가 유효하고 넘침0입니다. 코드와 사용법은 [native](../../../native/README.md#gguf-연산별-진단), [어댑터](../../../src/edge_asr/qwen/gguf.py)에 있습니다.

## 계측을 끈 새 빌드의 단계 시간

아래는 계측용 bridge의 **계측 끔** 평균입니다. node별 강제 분할이 없는 상태의 비용을 먼저 봅니다. 서로 다른 두 모델의 전사는 달라도 short는각4token/decode4회, long은각27token/decode27회였습니다.

| PCM /지표 | Q8/Q8 | Q4_K_M/Q4_0 |
| --- | ---: | ---: |
| short 전체 호출 | 0.810초 | 0.768초 |
| short 호출 시작→첫 token | **0.574초** | 0.611초 |
| short audio encoder | 0.315초 | 0.313초 |
| short LLM 음성 prefill | **0.165초** | 0.205초 |
| short 공통 text suffix | 0.094초 | 0.092초 |
| short token decode | 0.232초 | **0.152초** |
| long 전체 호출 | 2.945초 | 2.621초 |
| long 호출 시작→첫 token | **1.319초** | 1.507초 |
| long audio encoder | 0.805초 | 0.833초 |
| long LLM 음성 prefill | **0.413초** | 0.571초 |
| long 공통 text suffix | 0.100초 | 0.101초 |
| long token decode | 1.607초 | **1.096초** |

토큰당 decode는 Q8 short/long 약57.94/59.53ms, Q4 약38.11/40.58ms입니다. 같은 token 수에서도 생성은 줄었지만 첫 token 준비는 늘었습니다. 반면 입력 수집·VAD·큐·GUI/SSH 지연은 이번 고정 PCM 호출 시간에 포함하지 않습니다. 이 부분은 [이전 지연 경로 분석](realtime-both-q4-latency-20261113.md)을 유지합니다.

## 오디오 인코더 — 유지한 FP16 경로가 약 절반

아래 비율은 **계측 켬의 해당 단계 node 시간 합계 기준**입니다. 운영 시간 전체의 비율로 옮기지 않습니다. dtype은 node의 좌/우 입력으로, 항상 좌측이 가중치인 것은 아닙니다.

| Q4 audio encoder node 분류 | short | long |
| --- | ---: | ---: |
| FP16×FP16 MUL_MAT —합성곱3개 | 39.11% | 40.32% |
| FP16×F32 MUL_MAT —`a.conv_out.weight` 투영 | 6.96% | 6.42% |
| IM2COL —합성곱 입력 변환 | 3.53% | 3.49% |
| 위 세 경로 합계 | **49.61%** | **50.23%** |
| Q4_0×F32 MUL_MAT | 36.93% | 34.70% |

고정 upstream `tools/mtmd/models/qwen3a.cpp`의3회 conv2d→투영과 `ggml/src/ggml.c`의 conv2d→IM2COL/MUL_MAT 구성에 shape를 대조했습니다. FP16×FP16 node는 `node_5`/`node_15`/`node_25`이며, 두 번째 합성곱 행렬곱 `node_15` 하나가약24.7~24.9%였습니다. short에서 이 node의 좌/우 shape는 `[4320,1600,1,1]`/`[4320,480,1,1]`입니다. 소스 파일 해시는 JSON에 기록합니다.

이 네 FP16 가중치와 관련 경로는 앞선 audio Q4 변환에서 보존한 부분입니다. **Q4로 바꾼110개 행렬 밖에 큰 계산 비용이 남아 있습니다.** Q8에서도 같은 세 경로가약49.6~49.8%였고 양쪽 인코더 시간은 크게 줄지 않았습니다. 단순히 Q4 파일을 더 작게 만든 것만으로 인코더 전체가 빨라진다고 가정할 수 없습니다. 이번에 FP16 합성곱 양자화나 커널 변경을 적용한 것은 아닙니다.

## LLM prefill — quantized 행렬곱에 집중

| Q4 LLM 음성 prefill node 분류 | short | long |
| --- | ---: | ---: |
| Q4_K×F32 MUL_MAT | 75.67% | 69.57% |
| Q6_K×F32 MUL_MAT | 14.41% | 11.94% |
| 위 행렬곱 합계 | **90.08%** | **81.51%** |
| FLASH_ATTN_EXT | 5.13% | 13.68% |
| FFN 행렬곱 전체 —위 행렬곱의 일부 | 53.32% | 47.84% |

Q4_K_M에는 Q6_K도 포함됩니다. 큰 node는 여러 층의 `ffn_down`/`ffn_up`/`ffn_gate`로 분산돼 특정 한 층만의 문제는 아닙니다. Q4 decode에서는 `output.weight`가 node 시간의약28~29%였지만, 첫 token을 우선 줄이려면 그보다 앞선 audio/prefill을 구분해야 합니다.

관측으로 확인한 것은 **현재 실행에서 prefill의 quantized 행렬곱이 크고 Q8보다 오래 걸렸다**는 점입니다. 이 node에는 packing·활성값 양자화·dot/GEMM·동기화가 함께 들어갈 수 있어 dequant 비용만의 문제라고 확정하지 않습니다. 현재 소스의 `ggml-cpu/repack.cpp`에는 Q4_K/Q6_K의 NEON·DOTPROD 경로도 있으므로 단순히 ARM 최적화가 없다고 결론 내리지 않습니다. 실제 선택된 하위 커널과 그 내부 비용은 후속 dispatch/커널 계측 대상입니다. KleidiAI는 현재 빌드에서OFF이며 켬의 효과는 미측정입니다.

## 계측 자체의 영향

node별 관측은 graph를 나누고 매번 동기화합니다. fusion·스케줄·계산 형태가 달라질 수 있고 trace 생성/JSON 반환·Python 파싱도 비용을 더합니다.

| 계측 켬의 전체 호출 증가 —같은 빌드 끔 대비 | short | long |
| --- | ---: | ---: |
| Q8 | +15.58% | +7.56% |
| Q4 | +18.36% | +8.49% |

단계별 증가도 존재합니다. 예를 들어 Q4 encoder는약1.9~3.7%, prefill은약3.7~4.3% 늘었습니다. node 합계가 관측한 단계 wall time에서 차지하는 비율은약94.6~99.5%였으며, node 밖 기록/스케줄 잔여도 JSON에 분리했습니다. 실제 운영 시간에서 일정 오버헤드를 빼는 방식으로 보정하지 않습니다.

기존 bridge의 short/long 전체 평균은 Q8 0.792/2.875초, Q4 0.845/2.797초였습니다. 새 계측 bridge 끔과도 온도·순서가 달라 그대로 우열을 판단하지 않습니다. 계측 빌드를 운영 최적화로 채택하지 않았습니다.

| 실행 순서 | 시작 /최고 /종료 온도 | 현재 제한 플래그 |
| --- | ---: | ---: |
| 기존 Q8 | 59.50 /81.50 /72.15°C | 0 |
| 계측 Q8 | 72.15 /82.05 /72.70°C | 0 /8 |
| 계측 Q4 | 71.05 /84.25 /76.55°C | 0 /8 |
| 기존 Q4 | 77.10 /84.25 /74.35°C | 0 /8 |

600초/4GiB·온도 대기 없는 운영 guard와 전체 단계의 추론 잠금, 같은 부팅을 유지했습니다. 소표본·순차 실행·현재 열 제한을 포함한 관측입니다.

## 검증·보존·다음 대상

Host 전체427개 중425통과·선택 native2생략, Host/RP5 GGUF 관련29개씩 모두 통과했습니다. 여기에는 실제 모델 없는 profile 모드/실패 보존, C++ 집계의 callback 짝·shape 변경·역전 시계·8192그룹 넘침, 기존/계측 빌드 헤더 구문 검사가 포함됩니다. Ruff·포맷166파일·M1 보존·공유 설정3개·문서 링크·deploy 스크립트 구문을 확인했습니다. **RP5 실제 추론은64회 전부 성공했고**, 같은 모델의 모든 전사/token ID가 기존 연속 실행의 같은 PCM과 같습니다. 이것이 Q4의 기존 전체 CER 악화를 회복했다는 뜻은 아닙니다.

보호308파일·기존 engine6개 해시가 같고 새 원시/설정57파일의 Host/RP5 해시가 일치합니다. Host/RP5 독립 재집계도 byte 단위로 같습니다. 원시는 `results/optimization/opt3/operator-profile-20261113-01/`, 실행·분석·검사는 `results/maintenance/operator-profile-20261113-01/`에 보존합니다. 공유 JSON은 연산/dtype 상위5개·node 상위3개만 담고 전체 집계와 trace는 로컬 원시에 남깁니다.

다음 대상은 **FP16 합성곱/투영과 Q4_K/Q6_K prefill의 ARM 행렬 연산 경로**입니다. 현재 repack·스레드·shape에 따른 실제 dispatch를 확인하고 별도 커널/빌드 대조를 준비하는 순서가 타당합니다. 가중치를 더 낮추거나 KV 설정을 먼저 바꾸는 근거로 쓰지 않습니다. Q8 가변 기준·기존 설정과 정확도 판정은 유지하며 새 엔진 채택·추가 연속 평가·성능 개선은 아직 수행하지 않았습니다.
