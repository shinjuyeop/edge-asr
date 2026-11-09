# Qwen3-ASR-0.6B의 RP5 배포 경로 확인

2026-11-06. **sherpa-onnx 2.0.1 CPU + 배포 INT8 ONNX를 첫 검증 경로로 선정했다.** 이번은 배포 검토이며 새 ASR 추론은 0회다. [구현 근거·조건·다음 실행 절차](../../docs/archive/model-comparison/qwen3_deployment.md), [기계 판독 요약](qwen3-route-review-20261106.json).

## 확인한 범위

| 검사 | 확인 결과 |
| --- | --- |
| 소스·연결 | Host/RP5 모두 clean main `e7c7754`, origin/main 일치, `ssh rp5` 키 인증 성공 |
| RP5 환경 | Debian 13.3 / aarch64 / Cortex-A76 4코어 / RAM 8GB. 시스템 Python 3.13.5, 기존 `.venv` Python 3.11.18 |
| 기존 설치 API | sherpa-onnx/core 2.0.1의 `from_qwen3_asr`, Qwen config, stream `set_option` 존재. 모델 생성자는 호출하지 않음 |
| 네이티브 런타임 | sherpa 내부 ONNX Runtime 1.28.2. 별도 Python ORT 패키지 1.31.1과 구분 |
| 새 환경의 설치 후보 | PyPI의 sherpa-onnx 2.0.1 CPython 3.11 aarch64, core 2.0.1 py3 aarch64, NumPy 2.4.6 CPython 3.11 glibc aarch64 wheel·해시·yanked=false 확인. 새 환경 설치는 미실행 |
| 모델 수급 후보 | sherpa의 `sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2`. 릴리스 API 제공 크기 878,702,423bytes·SHA-256 확인. 다운로드·로컬 checksum 검증은 미실행 |
| 소스 검토 | sherpa v2.0.1 `af2d8d81`, exporter `0829a577`, Qwen 원본 안내·모델 카드, GGUF·C 대안 검토 |
| 실제 추론·성능 | 로딩/전사 0회. CER·RTF·로딩 시간·추론 CPU/RSS·Refine 지연 모두 null |

sherpa의 [공식 Qwen 문서](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/index.html)는 Linux aarch64와 한국어를 지원 대상으로 명시한다. [배포 모델 안내](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html)의 ONNX 변환본을 사용하되 원본 Qwen 모델과 변환 자산의 출처·라이선스·정밀도 검증 범위를 나누어 기록한다.

## 후속 실측에 앞서 고정할 조건

- Qwen 특징은 128차원이며 한국어는 stream별 `language=Korean`으로 전달한다. SenseVoice의 80차원 설정이나 `use_itn=false`를 복사하지 않는다. Qwen ITN 스위치는 미제공이므로 null/사유로 남긴다.
- 기본 KV 길이 512·출력 128 token은 초기 smoke 후보 값이다. [실제 인식 구현](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/offline-recognizer-qwen3-asr-impl.cc)에 입력 잘림과 KV 한계 종료가 있어, 전체 입력/출력 보존 여부를 확인한 뒤 본 평가 조건을 고정해야 한다.
- 별도 `.venv-qwen3-*`, `models/qwen3-*`, 고유 결과 ID를 사용한다. Host 구현·모의 검사 → main push → RP5 동기화 → 실제 짧은 WAV → 공통 KSS 50·`ko_cer_v1` 순서다.
- 기존 보호 예산·열 정책·실패 기록은 유지한다. batch 결과만으로 실시간 Refine 지연이나 자동 교체 적합성을 판정하지 않는다.

## 보존·근거 파일

이번에는 문서와 Git 제외 검토 파일만 추가했다. 기존 `.venv`·설치 패키지·모델·오디오·과거 결과·하드웨어 설정을 변경하는 명령은 실행하지 않았다. 원본 자산 전체의 전후 해시 대조는 이번 읽기 전용 검토 범위에 포함하지 않았으며, 새 환경/모델 추가 전에 별도 보존 기준을 저장한다. 과거 287개 테스트·29개 재집계 결과는 기존 기록으로 유지하며 이번에 재실행한 검사로 표시하지 않는다.

Host 원시 근거 경로는 `results/qwen3-route-review-20261106-01/`이다. RP5 환경/API 조회, PyPI JSON, 릴리스 자산 메타데이터, 읽은 구현 사본을 보관하며 각 SHA-256은 작은 JSON 요약에 있다. 배포처가 제공한 모델 digest와 로컬 근거 파일의 checksum은 서로 다른 검증 대상이다.

다음 관문은 **별도 실행 환경에서 기존 공식 3.5265초 WAV의 실제 로딩·전사·RSS 확인**이다. 프로젝트 Qwen 어댑터와 공통 평가 경로는 아직 구현하지 않았다.
