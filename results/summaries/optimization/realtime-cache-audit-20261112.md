# 반복 음성 처리·KV 재사용 분석

2026-11-12. 현재2/3 기준의 저장 로그를 분석했습니다. **KV 초기화 비용은 전체 추론의0.37%이며, 주요 비용은 음성 처리/prefill54.76%와 토큰 decode43.90%입니다.** 다음에 검증할 작은 후보는 음성 앞 고정 텍스트의 KV 유지입니다. 기존 음성 KV를 그대로 이어 쓰는 구현은 보류합니다. [기계 판독 요약](realtime-cache-audit-20261112.json), [현재2/3 실제100개 대조](realtime-split100-20261111.md).

이번 작업은 **기존 RP5 실측에 대한 Host/RP5 독립 재집계와 고정 소스 검토**입니다. 새 ASR 추론·캐시 구현·성능 측정은 수행하지 않았고 현재 모델·실행 설정은 유지합니다.

## 분석 대상과 비용

대상은 `realtime-split100-20261111-01/full100/split/worker.json`입니다. 실제 추론 소스 `976da90`·생성2T/음성·prefill3T·Q8·기존 split bridge·VAD/5초·첫1초/이후4초·토큰 표시·1배속 입력입니다. Zeroth60·FLEURS40·891.1448125초, 최종210회·부분119회·총329회 호출을 사용했습니다. 당시 CER428/3848=11.12%·실패/드롭0은 저장 점수의 참조이며 이번에 정확도를 재측정한 값이 아닙니다.

| 누적 비용 | 시간 | 전체 추론 합계634.318초 중 |
| --- | ---: | ---: |
| 음성 처리+LLM prefill | 347.376초 | 54.76% |
| 생성 토큰 decode | 278.481초 | 43.90% |
| KV 초기화 | 2.368초 | 0.37% |
| 음성 전처리/토큰화 | 0.153초 | 0.024% |
| 샘플링 /토큰 callback | 3.257 /2.418초 | 0.51 /0.38% |

합계는329개 직렬 호출의 비용이며1배속 입력 경과 시간이나 자막 반환 지연과 다릅니다. 부분 호출은138.818초(21.88%), 최종 호출은495.500초입니다. 부분 호출 비용을 모두 제거 가능한 시간으로 해석하지 않습니다. 첫 자막·지속 갱신을 유지하는 것이 목표입니다.

기존 `native_stderr`에329개 encoder 로그와329개 음성 embedding eval 로그가 남아 있어 새 계측 없이 보조 분해했습니다.

| 음성 처리+prefill 내부 | 기존 로그/경계 합계 |
| --- | ---: |
| 오디오 encoder 보고 시간 | 186.791초 |
| 오디오 embedding의 LLM 평가 보고 시간 | 98.350초 |
| 나머지 결합 경계 | 62.235초 |

앞의 두 값은 정수ms 해상도의 보조 로그입니다. 나머지는 text prefill·helper/호출 비용·해상도 차이를 포함하므로 **순수 text prefill 시간이나 캐시로 절약할 시간으로 표시하지 않습니다.** 기존 helper가 encoder 호출 후 embedding을 LLM에 전달하는 경계와 로그 위치를 확인했습니다. [고정 helper 구현](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd/mtmd-helper.cpp#L210).

## 얼마나 반복해서 처리했나

원음100개를 기존 `dataset.prepare_track`으로 다시 연결해 전체 float PCM 해시·원음 경계·manifest와329개 payload 해시를 확인했습니다. 모델 추론은 실행하지 않았습니다.

- ASR 호출에 전달한 PCM 합계771.548초, 호출 좌표의 합집합649.284초, 반복 처리된 좌표122.264초입니다. 전체 원음891.145초와 전사 구간의 좌표 합집합은 VAD 분할 때문에 서로 다릅니다.
- 부분→최종 대응119개 중118개는 같은 시작점의 엄격한 PCM 접두부입니다.1개는 최종 VAD 끝이 짧아져 부분 호출에만704프레임(44ms)이 더 있었습니다. 모든 부분 호출을 그대로 최종 입력의 접두부로 가정하지 않습니다.
- 호출 전체의 길이·float PCM SHA가 완전히 같은 중복은 **0개**입니다. 결과를 그대로 반환하는 exact PCM 캐시의 관측 적중도0개입니다.
- 반복 좌표는 전달량의15.85%이지만, 이를 임베딩 재사용 가능 비율이나 예상 속도 개선율로 바꾸지 않습니다.

## 캐시 종류별 판단

| 종류 | 현재 판단 |
| --- | --- |
| 한 호출 안의 토큰 decode KV | 이미 사용 중. 매 생성 토큰마다 전체 prompt를 다시 계산하는 구조는 아님 |
| 같은 전체 PCM의 결과/임베딩 캐시 | 분석한329회에서 동일 입력 재호출0개. 이번 자료로 이득을 뒷받침하지 못함 |
| 길어진 음성의 기존 embedding·KV 유지 | 현재 경로에서 PCM 접두부 일치만으로 안전성을 보장하지 못함 |
| 음성 앞 고정 text prefix KV 유지 | 다음 별도 실험 후보. 미구현·속도/메모리 이득 미측정 |

현재 bridge는 호출마다 `llama_memory_clear(..., true)`와 sampler reset 후 전체 음성을 처리합니다. 그 뒤 한 호출의 생성 중에는 누적 KV를 사용합니다. [프로젝트 bridge](../../../native/gguf/bridge.cpp).

길어진 음성의 단순 접두부 캐시는 다음 코드 근거 때문에 보류합니다. 고정 전처리는 입력 양끝 reflection과 전체 입력의 최대 log-mel 값에 따른 정규화를 사용합니다. 입력이 늘면 앞부분 특징이 같다는 보장이 없습니다. [고정 전처리](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd/mtmd-audio.cpp#L606). Qwen3A encoder는 기본 null mask의 `build_vit`으로 음성 윈도 내부를 처리하므로 새 음성이 앞 위치의 embedding에 영향을 줄 수 있다고 판단합니다. 이는 정적 코드에 따른 재사용 안전성 판단이며 변경된 embedding을 수치 비교한 실험은 아닙니다. [Qwen3A graph](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd/models/qwen3a.cpp#L72), [기본 mask](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd/mtmd-graph.h#L15), [attention 구현](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd/mtmd-graph.cpp#L560).

현재 고정 libmtmd의 `mtmd_support_audio_stream`은 Qwen3A를 지원 목록에 포함하지 않습니다. 다른 모델이나 다른 Qwen 실행 경로의 스트리밍 가능성을 부정하는 판정은 아닙니다. [고정 stream 지원 검사](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/tools/mtmd/mtmd.cpp#L4097).

## 다음 작은 실험

**음성 앞의 동일 텍스트만 유지하고 음성·음성 뒤 텍스트·이전 생성 토큰은 다시 계산하는 방식**을 검토합니다. 현재 모든 호출의 `prompt_positions - audio_tokens`는18이지만 이는 음성 앞뒤 텍스트 합계입니다.18개 전체가 재사용 가능한 prefix라는 뜻은 아니며, 실제 앞부분 토큰 수·별도 소요 시간은 아직null입니다.

별도 bridge·선택 설정으로 구현할 때는 첫 text chunk의 토큰·위치·모델/언어/디코딩 조건 일치를 검사하고, 그 이후 sequence 범위만 제거해야 합니다. 제거 실패·설정 변경·호출 실패 때는 전체 초기화로 복귀합니다. 고정 API는 위치 범위 삭제와 실패 반환을 제공하지만, 이것만으로 전사 동등성이나 속도 향상이 검증되는 것은 아닙니다. [고정 KV API](https://github.com/ggml-org/llama.cpp/blob/7d7f0287530bc8981690f361d8c1627dbae1c99a/include/llama.h#L761).

우선 text 앞/뒤·encoder·음성 embedding 평가 비용을 구분해 기록하고, 같은6개 연속 입력에서 캐시 끔/켬의 PCM·최종 전사·토큰 ID·첫 표시/최종 반환·실패·메모리를 대조하는 것이 다음 단계입니다. 이번 분석에서 캐시 예상 개선율이나 지연을 채우지 않습니다. 기존 기본값은 유지합니다.

## 검증·재현

공식 고정 커밋 `7d7f0287530bc8981690f361d8c1627dbae1c99a`의7개 파일을 내려받아 로컬 upstream과 바이트 일치를 확인했습니다. 프로젝트 코드6개는 측정 커밋 `976da90`의 코드와도 같습니다. 프로젝트 코드6개·upstream7개 및 기존 실험329파일의 해시를 전후 검사했습니다. Host/RP5에서 원음·payload·좌표·329개 native 보조 로그를 독립 재집계한 JSON은 바이트 단위로 일치했습니다. 분석 소스는 `0fc5149`이며 실제 추론 소스 `976da90`과 구분합니다.

분석 원시는 `results/optimization/opt3/realtime-cache-audit-20261112-01/`, 재현문·입력/소스 해시·공식 소스 사본·검사 로그는 `results/maintenance/realtime-cache-audit-20261112-01/`에 보존합니다. 양 장비에서 `.venv/bin/python`과 `PYTHONPATH=src`로 해당 `analyze.py`를 실행했습니다. 모델·패키지·원음·이전 결과·실행 코드는 변경하지 않았습니다. 문서 링크/CLI 검사만 수행하며 새 ASR 추론과 전체 회귀 테스트는 필요하지 않은 작업입니다.
