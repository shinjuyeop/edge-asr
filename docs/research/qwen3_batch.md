# Qwen3-ASR INT8의 첫 실제 실행과 공통 batch 비교 계획

2026-11-06. [경로 검토](qwen3_deployment.md) 이후 별도 `.venv-qwen3-onnx-20261106-01`에서 짧은 WAV 로딩·전사가 완료됐다. 실제 모델은 `models/qwen3-20261106-01/`에 보관하며 기존 환경·모델은 유지한다.

## 첫 실행

`e355604`의 `tools probe-qwen3`를 RP5에서 운영 열 정책과 4GiB 주소 공간 제한 아래 실행했다. `results/qwen3-smoke-20261106-01/`의 공식 WAV 3.5265초·1스레드·워밍업 0회에서 로딩 5.112187초, 첫 전사 3.401901초였고 관측 최대 RSS는 로딩 1422.266MiB·첫 전사 1508.984MiB였다. 이 시간에는 해당 프로브의 RSS 수집기 시작·종료 비용이 포함된다. 모델 파일을 먼저 해시했으므로 저장장치 cold start가 아니다. 단일 첫 호출을 steady-state 또는 실시간 지연으로 쓰지 않는다.

실제 그래프 검사는 Host의 별도 `.venv-qwen-inspect-20261106-01` / ONNX 1.19.1로 수행했고 추론은 하지 않았다. frontend FLOAT, encoder FLOAT/INT8, decoder FLOAT/UINT8 initializer와 encoder/decoder의 DynamicQuantizeLinear·MatMulInteger를 확인했다. decoder의 `cache_key_0`·`cache_value_0` 길이 축은 동적 `max_total_len`이다. graph inspection JSON·모든 모델/토크나이저 파일 해시·아카이브 checksum은 새 자산 경로에 보존한다.

## 입력 보존과 출력 정책

공통 Qwen 어댑터는 확인한 6개 파일의 SHA-256과 sherpa 2.0.1에 한정한다. 새 변환본이나 다른 런타임은 재검증 없이 같은 입력 길이 규칙을 적용하지 않는다. Python ORT를 별도로 설치하지 않아도 sherpa 네이티브 ORT 1.28.2로 실행됨을 새 환경에서 확인했다.

- 16kHz float PCM 전체를 전달한다. 한국어는 매번 새 stream에 `language=Korean`으로 지정한다. 특징은 128차원이다.
- [v2.0.1 offline-stream](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/offline-stream.cc)은 centered-STFT 프레임 수를 `floor(samples/160)` 이하로 제한한다. Qwen 구현의 100프레임 chunk마다 13 audio token, 나머지는 `ceil(remainder/8)` 규칙으로 trimming 전 상한을 구한다.
- 한국어 고정 prompt의 UTF-8 byte 수를 byte-BPE token 수의 보수적 상한으로 사용한다. 확인한 `<|audio_pad|>`는 단일 special token이다. prompt 상한+audio token 상한+최대 새 토큰+1이 KV 길이를 넘으면 추론 전에 실패 처리한다. 따라서 초 단위의 임의 cutoff로 원본을 자르지 않는다.
- 기본 KV 512·최대 새 토큰 128·temperature 1e-6·top_p 0.8·seed 42·hotwords 없음으로 고정한다. native stderr의 잘림/반복 경고 또는 반환 token 상한 도달은 실패로 처리하며 text/tokens/경고를 보존한다. native의 반복 정리·UTF-8 정리 등을 적용하기 전 생성 토큰 전체를 확보했다고 주장하지 않는다.
- ITN은 스위치 미제공을 null로 표시한다. 추가 텍스트 교정 없이 native text를 공통 `ko_cer_v1`로 채점한다. 실패 원문은 `adapter_result`에 남기며 성공 CER에 섞지 않는다.

## 결과 확인 전에 고정하는 첫 비교

`tools compare-qwen3`는 **구성별 1회인 탐색 비교**를 수행한다. 스레드 최적값·반복 안정성·독립 평가·실시간 채택을 확정하지 않는다.

| 조건 | 고정값 |
| --- | --- |
| 데이터 | 기존 KSS 개발 50개 manifest·WAV·expanded_script, 원음 16kHz mono PCM16, 입력 해시 재검증 |
| 정규화 | 동일 `ko_cer_v1`, 문자 가중 corpus CER·성공률·문자 커버리지 |
| 모델·환경 | Zipformer, SenseVoice INT8, Qwen INT8. 모두 새 후보 환경의 같은 Python 3.11.18 / NumPy 2.4.6 / sherpa/core 2.0.1·native ORT 1.28.2 |
| 실행 | 모델/스레드마다 새 프로세스·한 번 로딩·첫 발화 워밍업 3회·50개 순차 추론 |
| 순서 | 1T: Qwen→Zip→Sense, 2T: Sense→Qwen→Zip, 4T: Zip→Sense→Qwen. 각 구성 1회 |
| 열·자원 | 모든 구성에 operational 정책. 시작 53°C 이하·현재 제한 없음 3회, timeout 600초·가상 주소 공간 4GiB·core dump 0·실제 PID 제한 확인. 온도/감속은 관측하고 저전압·계측 실패 등은 거부 |
| 실패 | 실패 case의 원시 결과를 유지하고 캠페인을 중단. 나머지는 미실행. 성공한 파일만의 CER/RTF에는 성공률을 함께 표시 |
| 해석 | 공통 batch 처리 시간·CPU/RSS·로딩 분리. 고정 클럭 통제 비교가 아니며 실제 온도/감속과 함께 해석. batch RTF를 Refine 지연으로 대체하지 않음 |

모델과 스레드의 모든 조합은 `plan.json`에 설정 해시·입력 해시·Qwen 입력 예산과 함께 먼저 저장한다. 기존 `tools compare`의 2모델 반복 캠페인·과거 요약은 변경하지 않는다. 실행 후 raw를 Host로 회수하고 같은 scorer로 9개 구성의 재채점을 대조한다.

```bash
# Host 테스트·main push → RP5 pull 후, RP5의 새 환경에서만 실행
PYTHONDONTWRITEBYTECODE=1 .venv-qwen3-onnx-20261106-01/bin/python \
  scripts/edge_asr.py tools compare-qwen3 \
  --output results/comparisons/qwen3-kss-dev50-20261106-01
```

Refine 후보 검토는 CER·개별 악화·batch 처리 비용을 바탕으로 한다. 온라인 VAD 구간·Zip 동시 부하·구간 끝→반환·드롭/백로그·마이크는 별도 실측이며 자동 교체 보류를 유지한다.
