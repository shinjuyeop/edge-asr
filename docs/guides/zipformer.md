# 한국어 Zipformer B0 실행 기준

RP5의 주력 경로는 한국어 Zipformer 174M, INT8 ONNX, sherpa-onnx의 상태 유지형 CPU 전사입니다. 게시명은 174M이며 모델 카드의 실제 규모는 약 155.7M입니다. B0는 Greedy Search, 320ms 모델 전진, 100ms PCM 공급, 모델 2스레드, 1.2초 trailing-blank endpoint입니다. 인식기 스트림이 encoder cache를 유지하며 긴 입력을 동일 상태로 처리합니다. WAV 끝에 0.64초의 모델용 padding을 공급한 뒤 EOF를 배출합니다. RP5 비교의 CPU 상한은 1.5GHz입니다. [B0 모델 설정](../../configs/zipformer/b0.toml) · [시스템 구조](../reference/architecture.md)

## 모델과 환경

- 모델 리비전은 `f0e73b1653c3ea75898c6d949dd71c690c9121da`입니다. `chunk-16-left-128` encoder·decoder·joiner INT8 및 `tokens.txt`를 설정의 모델 디렉터리에 둡니다. [게시 자산](https://huggingface.co/kangkyu/icefall-asr-ko-streaming-zipformer-174m/tree/f0e73b1653c3ea75898c6d949dd71c690c9121da)
- 환경은 RP5 8GB ARM64, 프로젝트 `.venv` Python 3.11.18, sherpa-onnx/core 2.0.1과 내장 ONNX Runtime 1.28.2입니다. CPU 제공자로 실행하며 Hailo-8L 호출은 없습니다. [장비와 패키지](../reference/environment.md)
- 이전 동등 구성은 `configs/model_comparison/zipformer_174m_320ms.toml`에서 기본 Greedy를 사용했습니다. 당시 추론 소스는 `4486b7e`, 실측 원시는 RP5 `results/optimization/asr-comparison-20261127-01/`의 `replay-fixed100-zipformer_174m_320ms`, `replay-seoul8-zipformer_174m_320ms`, `replay-reading4-zipformer_174m_320ms`, `replay-independent-zipformer_174m_320ms`, `replay-stress-zipformer_174m_320ms`입니다. 개발 PC는 같은 실험 경로의 `rp5/`에 해시 대조본을 보관합니다. [B0 근거 실측](../../results/summaries/asr-rp5-model-comparison-20261127.md)

| 가중치 | SHA-256 |
| --- | --- |
| encoder INT8 | `e595e2e37f46078387868ffa656b775de787fa511cf851cf8e1892d7dbdabe54` |
| decoder INT8 | `f5dfa6c8609b29da86c739d4904889475c33b62e28e70c619952a0ee6c31416f` |
| joiner INT8 | `64efd9aeb71fb2278c713b3d5eb5ec45edf6b2ccd8716d3709044f17723c13fd` |
| tokens.txt | `435dfb9e0a2b6a79124f1a4d8f0f33a951b25384726e2e0d854f081533e6ec9d` |

## 대표 실행

검증용 RP5에서 CPU 상한을 1.5GHz로 관리하고 기존 열 보호를 적용합니다. `manifest`에는 16kHz 모노 PCM·정답·순서를 고정한 JSONL을, `run`에는 새 출력 경로를 지정합니다. 기존 실측의 고정 100개·서울·낭독·Common Voice는 파일마다 2초 영점을 공급했습니다. 30분 명세는 WAV 자체에 간격이 들어 있으므로 `--gap-s 0`, `--timeout-s 2600`을 사용합니다. [RP5 실행·회수 절차](deployment.md)

```bash
.venv/bin/python scripts/edge_asr.py tools thermal-run \
  --output "$run" --timeout-s 1300 --temperature-policy operational -- \
  .venv/bin/python scripts/edge_asr.py tools replay-asr \
  --config configs/zipformer/b0.toml --manifest "$manifest" --output "$run" \
  --gap-s 2 --warmup-max-s 2.5
```

청취 서버에는 [B0 청취 프로필](../../configs/listener_zipformer_b0.toml)을 `tools listen-qwen3 --port 8765 --profile configs/listener_zipformer_b0.toml`로 전달합니다. 서버 준비 시 기존 기준선의 고정 100개 명세와 음원을 확보합니다. [SSH 터널과 포트 8765](listener.md)

## B0 실측과 후속 검증

| 1배속 측정 | B0 |
| --- | ---: |
| 고정 100 CER / 첫 본문 p95 / 끝→확정 p95 | 9.80% / 3.36초 / 1.34초 |
| 서울 8 CER / 공통 주석 단어 표시 p95 / 전체 갱신 p95 | 8.17% / 0.89초 / 1.00초 |
| 연속 낭독 4 CER | 7.24% |
| Common Voice 24 CER | 12.58% |
| 30분 28초 CPU / 최고 온도 / decode step p95 | 18.6% / 79.3°C / 68.14ms |
| 30분 28초 드롭·실패·확정 유실 | 0·0·0 |

B1은 같은 가중치에서 Modified Beam Search의 최대 활성 경로 2·4·8을 비교합니다. B2 이후에는 남은 오류에 따라 저음량·첫 토큰·고유명사, 발화 확정과 띄어쓰기·구두점, 장시간 표시 데이터 수명, encoder 연산을 검증합니다. 구현과 P8·T의 기록은 Qwen 비교 자료로 보존하고 Qwen 최적화는 보류합니다.
