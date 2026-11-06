# Qwen3-ASR-0.6B의 RP5 CPU 배포 경로 검토

이 문서는 모델 수급 전의 경로 선정 기록이다. 이후 별도 환경 설치·실제 추론·공통 평가를 완료했으며 현재 수치와 한계는 [KSS 실측 결과](../../results/summaries/qwen3-kss-dev50-20261106.md)에서 확인한다.

2026-11-06. 기준 소스 `e7c7754`. **첫 검증 경로는 sherpa-onnx 2.0.1 CPU + 배포 INT8 ONNX로 선정한다.** 이번 단계는 공식 문서·구현·배포 메타데이터와 RP5의 설치 API를 확인한 검토다. 새 환경 설치·모델 다운로드·로딩·전사·성능 측정은 아직 수행하지 않았다. [확인 기록](../../results/summaries/qwen3-route-review-20261106.md).

## 경로 선택 근거

| 경로 | 확인한 근거 | 이번 결정 |
| --- | --- | --- |
| Qwen 공식 `qwen-asr` / Transformers·vLLM | [Qwen 구현 안내](https://github.com/QwenLM/Qwen3-ASR)와 [0.6B 모델 카드](https://huggingface.co/Qwen/Qwen3-ASR-0.6B). 한국어를 지원하고 원본 모델 라이선스는 Apache-2.0. 공식 예시는 PyTorch·GPU 구성이며 streaming API는 vLLM 경로 | 원본 동작을 확인하는 참고 경로. RP5 ARM64 CPU용 양자화 배포본·의존성을 별도로 검증해야 하므로 첫 실행에는 선택하지 않음 |
| **sherpa-onnx CPU / INT8 ONNX** | [sherpa 문서](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/index.html)에 Linux aarch64와 한국어 지원, [배포 모델](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html), Python Offline API가 있음. 실제 RP5의 2.0.1에도 생성 API가 존재 | **우선 경로.** 기존 평가기의 Offline 계약을 그대로 확장할 수 있고, 기존과 같은 버전을 별도 환경에 설치 가능 |
| GGML / GGUF | [predict-woo/qwen3-asr.cpp](https://github.com/predict-woo/qwen3-asr.cpp)는 Q8_0와 Linux 빌드를 제공. 문서 성능 예시는 Apple M2 Pro 기준 | ONNX 경로가 메모리·실행 제한을 통과하지 못하면 검토할 별도 후보. GGML 빌드·Python 호출 경계·프로세스 계측은 추가 작업 |
| C / BLAS | [antirez/qwen-asr](https://github.com/antirez/qwen-asr)는 Linux OpenBLAS 및 NEON 구현이 있음. 확인한 CPU 경로는 BF16 원본 가중치를 사용하며 README의 Q8 설명은 GPU 백엔드에 해당 | 이번 CPU 양자화 검증의 우선 경로로 선택하지 않음 |

이 결정은 **배포 가능성을 확인할 첫 구현을 고른 것**이다. RP5에서의 정확도·속도·메모리 우위를 입증한 결과가 아니다. 다른 장비의 처리량·메모리 수치를 RP5 예상값으로 옮기지 않는다. 일반 텍스트용 `Qwen3-0.6B` GGUF와 음성용 `Qwen3-ASR-0.6B`도 구분한다.

## 실제 환경과 설치 가능성

Host와 RP5는 모두 clean `main/e7c7754`였고 origin/main도 일치했다. RP5는 Debian 13.3 / aarch64 / Cortex-A76 4코어 / RAM 8GB다. 시스템 Python은 3.13.5이며 기존 프로젝트 `.venv`는 별도 Python 3.11.18을 사용한다. Host `.venv`는 Python 3.11.14다.

RP5에서 기존 환경을 읽기 전용으로 import하여 다음을 확인했다. 모델 생성자는 호출하지 않았다.

- `sherpa-onnx==2.0.1`, `sherpa-onnx-core==2.0.1`, `numpy==2.4.6`.
- `OfflineRecognizer.from_qwen3_asr`, `OfflineQwen3ASRModelConfig`, `OfflineStream.set_option` 존재.
- sherpa 네이티브 ONNX Runtime **1.28.2**. 별도 Python `onnxruntime` 패키지 **1.31.1**과 구분한다. 후자를 설치하거나 변경하는 것만으로 sherpa 내부 런타임을 변경할 수 있다고 가정하지 않는다.
- 별도 3.11 인터프리터: `/home/deepet/.local/share/uv/python/cpython-3.11-linux-aarch64-gnu/bin/python3.11`. `ensurepip --version` 조회도 성공했다. 기존 `.venv`에는 pip 모듈이 없으며 추가 설치하지 않았다.

[PyPI sherpa-onnx 2.0.1](https://pypi.org/project/sherpa-onnx/2.0.1/#files), [core 2.0.1](https://pypi.org/project/sherpa-onnx-core/2.0.1/#files), [NumPy 2.4.6](https://pypi.org/project/numpy/2.4.6/#files)의 배포 JSON에서 아래 wheel과 SHA-256·yanked=false를 확인했다. **wheel 메타데이터 확인은 새 환경에서의 설치 성공과 구분한다.**

| 패키지 | 선택할 ARM64 wheel |
| --- | --- |
| sherpa-onnx 2.0.1 | `sherpa_onnx-2.0.1-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` |
| sherpa-onnx-core 2.0.1 | `sherpa_onnx_core-2.0.1-py3-none-manylinux2014_aarch64.whl` |
| NumPy 2.4.6 | `numpy-2.4.6-cp311-cp311-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl` |

새 환경에서는 이 세 패키지의 wheel을 고정하고 설치 스냅샷·해시·`pip check`를 남긴다. 기존 환경 복제·업그레이드, 시스템 Python 교체, PyTorch·vLLM 추가 설치는 이번 선택에 필요하지 않다.

## 모델 출처와 정밀도 표시

선정 자산은 [sherpa-onnx asr-models 릴리스](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models)의 `sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2`다. Qwen 원본을 Wasser1462의 변환 코드로 내보낸 **sherpa 프로젝트 배포본**이다.

2026-11-06 GitHub 릴리스 API의 해당 자산 메타데이터:

- 압축 파일 크기: **878,702,423 bytes**. 모델 RSS나 압축 해제 후 크기를 뜻하지 않는다.
- 배포처 제공 SHA-256: `393f8a14e2f5fb96746aaab342997a40641001fbd5bf9592a080a8329178ee96`.
- 아직 다운로드하지 않았으므로 **로컬 파일의 SHA-256 검증은 미실행**이다. 수급 시 위 값과 대조하고 내부 파일도 각각 해시한다.
- 필요한 파일: `conv_frontend.onnx`, `encoder.int8.onnx`, `decoder.int8.onnx`, `tokenizer/vocab.json`, `tokenizer/merges.txt`, `tokenizer/tokenizer_config.json`. 동봉 README·라이선스 참조도 보존한다.

변환 코드는 [`0829a577`의 export_qwen3_asr_onnx.py](https://github.com/Wasser1462/Qwen3-ASR-onnx/blob/0829a577ee741e408e65763dfe01ff4fe75408e9/export_qwen3_asr_onnx.py)를 읽었다. encoder에 dynamic QInt8, decoder에 dynamic QUInt8 및 per-channel 옵션을 지정한다. **이는 읽은 변환 코드의 동작이며 배포 자산을 그 설정으로 생성했다는 검증은 아니다.** 파일명의 INT8 선언·변환 코드·실제 ONNX 그래프 dtype를 구분하고, 아직 그래프를 읽지 않은 정밀도와 배포 양자화 방법은 미확인으로 기록한다. 원본의 Apache-2.0와 별개로 변환본의 동봉 고지·출처를 확인한다.

## API와 정확도 비교에 영향을 주는 조건

기준은 sherpa-onnx v2.0.1 태그 커밋 `af2d8d81dbfa61c4cea4c568da78f3982edca005`의 [Python factory](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py), [Qwen 인식 구현](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/offline-recognizer-qwen3-asr-impl.cc), [모델 구현](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/offline-qwen3-asr-model.cc)이다. RP5에 설치된 factory의 시그니처·본문도 별도로 보존했다.

| 항목 | 후속 smoke·어댑터의 고정 조건 |
| --- | --- |
| 입력·특징 | 기존 16kHz mono PCM16을 공통 로더에서 float PCM으로 전달. Qwen 특징은 **128차원**. 외부 RMS 정규화·resampling·무음 padding을 추가하지 않음 |
| 언어 | 발화마다 새 stream을 만들고 `stream.set_option("language", "Korean")`. factory 인자에는 language가 없지만 native 구현은 stream option을 읽어 언어 prefix를 구성함. 설정 `ko`와 실제 전달값 `Korean`을 함께 기록하고 실제 동작은 smoke에서 확인 |
| ITN | Qwen factory/model config에 SenseVoice의 `use_itn` 스위치가 없음. `use_itn=null`과 사유를 기록하고 모델 고유 숫자 표기를 보존. `false`를 적용했다고 표시하지 않음 |
| 디코딩 | CPU, `greedy_search`, temperature `1e-6`, top_p `0.8`, seed `42`, hotwords 빈 문자열. 구현에서 temperature가 `1e-6` 이하이면 argmax 경로를 사용 |
| 호출 계약 | 한 번 load, 발화별 create_stream → 언어 지정 → accept_waveform → decode_stream → result. Zipformer의 tail padding·streaming flush를 적용하지 않음 |
| 출력 보존 | native가 반환한 text·tokens·설정·stderr를 보존. native 내부의 언어 prefix 정리·UTF-8 replacement 제거·반복 tail 정리는 출력 정책으로 명시. native 반환 text를 생성 토큰 전체 원문이라고 표시하지 않음 |
| 지원 모드 | 이번 경로는 Offline batch. VAD를 붙인 완료 구간 전사는 별도 Refine 실험이며 네이티브 streaming·부분 자막 지원으로 표시하지 않음 |

초기 smoke 후보는 1스레드, `max_total_len=512`, `max_new_tokens=128`이다. 이 값은 설치된 factory 기본값이며 최적값이나 적정 메모리의 예측값이 아니다. 본 KSS 평가의 token 예산은 아래 입력 보존 검사를 통과한 뒤 사전에 고정한다.

### 입력·출력 잘림을 확인할 관문

모델 구현은 decoder KV 입력의 고정 shape가 있으면 이를 우선하며, 동적 shape일 때 설정 길이를 사용한다. 설정만 크게 올려 고정 export 한계를 늘릴 수 있다고 가정하지 않는다. 인식 구현은 prompt+audio가 한계를 넘으면 **음성 특징을 잘라 처리**한다. 생성 중 KV 길이에 도달하면 종료하고, 새 토큰 상한 부근에서는 잘림 경고를 남긴다. 정상 종료나 비어 있지 않은 text만으로 완전한 전사를 판정할 수 없다.

따라서 짧은 WAV 이후 본 평가 전에 실제 그래프의 KV shape·입력 프레임→audio token 규칙·prompt 길이·출력 여유를 확인한다. stderr 잘림 경고, KV 한계 종료, token 상한 여부를 기록할 수 있어야 한다. 입력 길이 제한은 추정한 초 단위 상수로 채우지 않는다. 한계를 넘는 파일은 원본을 임의로 자르거나 뒤를 버려 성공 처리하지 않는다. 필요한 경우 별도 export 또는 사전에 고정한 구간 정책을 별도 실행 ID로 검토한다. 전체 WAV 평가와 구간 평가를 섞지 않는다.

## 후속 실행 순서와 보존 경로

아래는 **아직 만들지 않은 예정 경로의 예시**다. 이미 존재하면 덮어쓰지 않고 새 ID를 사용한다.

| 종류 | 경로 예시 |
| --- | --- |
| 별도 RP5 환경 | `.venv-qwen3-onnx-20261106-01/` |
| 수급 자산·압축 파일 | `models/qwen3-20261106-01/` 아래 공식 아카이브와 원래 내부 디렉터리 |
| 실제 짧은 WAV 검사 | `results/qwen3-smoke-20261106-01/` |
| 공통 KSS 평가 | `results/evaluations/qwen3-kss-dev50-20261106-t1-01/` 등 조건별 고유 ID |

1. **별도 환경·자산 수급:** 기존 `.venv`·모델·data·결과의 보존 기준을 먼저 저장한다. 위 인터프리터로 새 환경을 만들고 wheel만 고정 설치한다. 배포처 checksum, 내부 파일별 해시·license/provenance를 기록한다. exporter 실행·재양자화는 첫 경로에 필요하지 않다.
2. **Host 구현·모의 검사 → main commit/push → RP5 동기화:** 짧은 WAV 검사 도구를 먼저 검증·배포한다. runtime import를 지연하고 환경·실행 ID·로딩/전사/자원·실패를 보존한다. 초기 도구 성공을 실제 ARM64 추론 성공으로 표시하지 않는다.
3. **RP5 짧은 WAV:** 기존 공식 `0.wav`(3.5265초) 1회로 로딩·한국어 전달·전사 반환·RSS·native ORT를 확인한다. 최초 실행과 워밍업 후 batch 비용을 구분하며, 1회에서 분위수나 모델 순위를 만들지 않는다. 로딩 실패·할당 실패·timeout도 해당 실행에 남긴다.
4. **공통 batch 통합:** `model_files/load/transcribe/close`, factory, 설정, 미지원 모드 거부와 모의 계약 검사를 추가한다. 모델 파일 외 tokenizer의 실제 파일들도 해시 목록에 포함한다. 현재 `qwen3_asr`는 legacy 설정 이름만 있으며 공통 평가기는 계속 거부하는 것이 현 상태다.
5. **같은 KSS 비교:** 기존 KSS 50·`expanded_script` 정답·오디오/manifest SHA-256·원음·`ko_cer_v1`를 유지한다. 워밍업 3회·1/2/4스레드와 반복/교차 순서·token 예산을 실행 전에 고정한다. CER·성공률·정답 문자 커버리지·batch RTF·CPU/RSS·로딩을 함께 보고하고 raw 회수·Host 재채점을 대조한다. 기존 수치는 과거 측정으로 분리하고 필요 시 같은 현재 조건의 기준선도 새 ID에서 측정한다.
6. **Refine 검토:** batch 통과 뒤 같은 온라인 VAD 구간의 정확도·짧은 발화/무음·Zip 동시 CPU/RSS·큐/드롭·구간 끝→반환 지연을 실제로 측정한다. 자동 교체는 기존 보류를 유지한다. 마이크는 장치 준비 후 별도 확인한다.

실제 추론은 기존 [열 정책](m3_thermal_policy.md)의 시작 조건·외부 timeout·가상 주소 공간 4GiB·core dump 0·실제 PID 제한 확인을 유지한다. 4GiB는 보호 예산이며 필요 RSS의 추정값이 아니다. 제한 실패·할당 실패 시 무제한 재시도하지 않고 결과에 따라 경로를 재검토한다. 운영 정책과 통제 성능 비교의 판정도 구분한다. 하드웨어·governor·팬·전압 설정은 변경하지 않는다.

## 이번 검토의 근거 보관

Host의 Git 제외 `results/qwen3-route-review-20261106-01/`에 RP5 환경/API 조회 JSON, PyPI 배포 JSON, GitHub 모델 자산 메타데이터, 읽은 주요 구현 사본을 저장했다. 작은 [JSON 요약](../../results/summaries/qwen3-route-review-20261106.json)에는 이 파일들의 SHA-256을 기록한다. 새 ASR 호출 수는 0이며 CER·RTF·로딩 시간·추론 CPU/RSS·실시간 지연은 모두 미측정이다.
