# 한국어 자막 최적화 과정

## 파일 전사에서 연속 자막으로

고정 100개(Zeroth 60개·FLEURS 40개)를 파일별로 전사하면 발화 도중 언제 첫 글자가 보이고 다음 글자까지 얼마나 기다리는지 알 수 없었다. RP5에 100ms 단위로 WAV를 1배속 공급하고 입력과 추론을 분리했다. VAD 분기에만 음량을 보정해 조용한 발화를 검출하고, 인식에는 원음을 전달했다. 완성 문자열을 기다리던 화면은 생성 토큰부터 표시하고, 같은 접두부를 다시 생성하는 동안에는 중복 표시를 보류하도록 했다. 배치는 같은 PCM의 연산·전사 차이를 진단하는 대조로 남겼다. [배치 기준](fixed100_baseline.md) · [VAD 대조](../../results/summaries/optimization/realtime-accuracy-20261110.md) · [표시 안정성](../../results/summaries/optimization/realtime-stability-20261111.md)

## 디코더를 줄이면 어디까지 빨라지는가

같은 GGUF 빌드와 Q8 음성 Encoder에서 텍스트 디코더를 Q4로 줄였다. 긴 원음 12개에서 추출한 확정 PCM 233개의 호출 합은 22.0% 짧아졌다. 그러나 같은 원음의 1배속 대조에서는 CER가 12.03%에서 15.73%로 올랐고 확정 실패가 0건에서 7건으로 늘었다. 출력층만 Q6_K로 높여도 실패 7건이 남았다. 이 때문에 정확도·완료율의 기준은 Q8로 두고 빠른 Q4를 속도 비교에 사용한다. [동일 PCM](../../results/summaries/optimization/decoder-pair-full-20261118.md) · [1배속 대조](../../results/summaries/optimization/decoder-pair-stream-20261118.md) · [출력층 대조](../../results/summaries/optimization/decoder-headroom-20261118.md)

## CPU 병목과 재사용할 수 있는 상태

음성 인코딩·Prefill과 토큰별 Decode에서 유리한 스레드 수가 달랐다. 공유 CPU 구성은 생성 2·음성/Prefill 3스레드를 사용한다. Q8 가중치 사전 읽기는 두 거리에서 일관된 이득이 없었다. CPU 합성곱 타일도 같은 확정 전사를 유지했지만 긴 원음에서 전체 추론 감소는 0.86%였고 자연발화의 첫 4자가 늦어졌다. [스레드 대조](../../results/summaries/optimization/realtime-split100-20261111.md) · [커널 대조](../../results/summaries/optimization/prefetch-q8-20261117.md) · [합성곱 대조](../../results/summaries/optimization/q8-conv-headroom-20261118.md)

음성 앞 고정 9토큰의 KV는 다음 요청에서도 유효했다. 이를 재사용해 고정 100개에서 확정 210구간의 PCM·토큰을 유지하면서 전체 추론 합을 9.16% 줄였다. 반면 음성은 입력이 늘 때 특징 정규화와 Encoder 문맥이 달라진다. 독립 창의 임베딩·음성 KV를 재사용한 Q8 실험은 재사용 범위가 작았고 자연발화 갱신 공백이 늘었다. 이전 부분 토큰을 확정의 후보로 검증한 방식도 생성 단계의 절감이 전체 지연 개선으로 일관되게 이어지지 않았다. [텍스트 KV 채택](../../results/summaries/optimization/realtime-prefix-cache-20261112.md) · [음성 창 캐시](../../results/summaries/optimization/q8-cache-headroom-20261119.md) · [토큰 후보 검증](../../results/summaries/optimization/token-draft-block2-20261120.md)

## Hailo-8L에 맡길 범위

Conv2 단독부터 시작해 Conv1·2·3과 GELU를 한 HEF로 묶었다. 실클럭이 비슷한 CPU 대조에서 확정 추론 합이 607.67초에서 508.69초로 줄어 청취 구성에 CNN 묶음을 선택했다. 당시 오류는 419자에서 450자로 늘었다. 출력 투영까지 묶은 경로는 전체 요청 합을 더 줄였지만 부분 요청 수도 줄고 첫 표시와 최신 갱신은 늦어졌다. 첫 Transformer 블록도 제한된 길이에서 실행했으나 고정 shape, 재배열·형식 변환, PCIe 전송·동기화 비용과 수치 오차를 함께 감당해야 한다. 추가 이식은 연속 입력에서 얻는 이득을 확인할 때까지 보류했다. [CNN 실측](../../results/summaries/optimization/hailo-cnn-stack-20261123.md) · [블록별 비용·컴파일·전사](../../results/summaries/optimization/hailo-partitions-20261123.md)

## T는 갱신 공백을 줄였고 P8은 분할 오류를 줄였다

5초 CNN 기준에서 첫 부분 요청이 확정 작업 뒤로 밀리거나 종료 직전 보류돼 자막이 멈췄다. T는 첫 부분을 0.45초부터 허용하고 최대 구간을 4초로 줄였다. 짧은 쉼 뒤에는 앞에서 소유하지 않은 음성만 문맥에 포함했다. 고정 100개에서 5초 초과 갱신 공백은 16회에서 0회로 줄었지만 강제로 자른 한 발화를 두 번 독립 전사하면서 ‘매일’이 ‘매. 매일’로 바뀌기도 했다. 반복이 확인된 9쌍은 PCM을 이어 같은 모델로 읽자 모두 개선됐다. [T의 요청·표시 분석](../../results/summaries/optimization/subtitle-continuity-20261123.md) · [분리·결합 진단](../../results/summaries/optimization/boundary-20261124.md)

P8은 3.4초부터 음향적 쉼을 찾아 최대 8초까지 문맥을 보존했다. 같은 고정 100개에서 강제 상한 절단은 107회에서 8회로, 정답에 없는 경계 반복은 9회에서 4회로 줄었다. 대신 긴 입력이 직렬 워커를 점유해 첫 본문과 확정이 늦어졌다. 5초 P5G는 더 빠르지만 경계 반복 9회가 남았다. 반응성에는 T, 경계 정확도에는 P8을 선택한 이유다. [경계별 전사와 지연](../../results/summaries/optimization/boundary-20261124.md)

## 중첩·재전사의 한계와 남은 병목

서울 자연발화 8개의 강제 경계에 이미 소유한 200ms를 붙인 Host 배치 진단에서 삽입이 26자에서 56자로 늘었다. 정답 없이 인접 전사의 끝·시작 글자가 겹치는 쌍만 합쳐 RP5에서 다시 읽자 고정 T의 오류는 460자에서 449자로 줄었지만 추가 호출에 33.97초가 들었다. 자연발화 T에서는 선택된 쌍이 없었고 낭독에서는 한 쌍이 악화됐다. 어느 실험도 라이브 확정 수정을 포함한 지연을 검증하지 않아 일괄 중첩과 자동 복구를 보류했다. [200ms 중첩 진단](../../results/summaries/optimization/subtitle-continuity-20261123.md) · [선택적 재전사](../../results/summaries/optimization/boundary-20261124.md)

CPU에는 나머지 Encoder와 Prefill·직렬 Decode가 남아 있다. 확정을 기다리거나 취소될 부분 요청을 처리하는 동안 새 본문 표시가 늦어지고, 열 보호는 부분 요청을 더 미룰 수 있다. 엔진 비교에서는 배치 호출 비용과 실제 1배속 표시 지연을 따로 검증한다. [현재 우선순위](../status.md)

실행별 수치와 실패 기록은 [보고서 색인](../../results/summaries/optimization/README.md), 초기 33단계의 세부 실험 순서와 당시 판단은 [정리 전 최적화 과정](https://github.com/shinjuyeop/edge-asr/blob/619b0422c2f72c6fe4480b39f7ca32e2f848aba4/docs/research/optimization_history.md)에 있다.
