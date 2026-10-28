# edge-asr

Raspberry Pi 5(ARM64, Debian 13, RAM 8GB)에서 한국어 ASR 모델을 비교하고 실시간 배포하기 위한 연구개발 저장소입니다. Host PC에서 개발·단위 검사를 수행하고, 실제 추론·벤치마크는 RP5에서 수행합니다. 기준 인터프리터는 Python 3.11입니다.

초기 골격, 패키징, 설정 검사 CLI, 기존 프로브의 반복 실행·집계 도구, 벤치마크 설계, 배포 절차 및 실험 원본 보존을 제공합니다. 상주 모델을 사용하는 공통 추론 실행기, 모델 어댑터, 마이크 입력, 서비스 배포는 아직 구현하지 않았습니다. 이번 작업에서 RP5에 연결하거나 추론 결과를 재검증하지 않았습니다.

초기 구성의 실제 로컬 검사 결과는 [검증 기록](docs/verification.md)에 있습니다.

다음 RP5 작업은 [반복 벤치마크 실행 안내](docs/rp5_repeat_benchmark.md)를 따릅니다. 새 소스 배포 후 기존 RP5 가상환경·모델을 지정하여 1·2·4스레드 워밍업·반복 실행, p50/p95 및 프로세스 CPU·RSS를 기록합니다. 원본 프로브나 기존 패키지를 변경하지 않습니다.

## 디렉터리

| 경로 | 역할 |
| --- | --- |
| `src/edge_asr/` | 설치 가능한 공통 Python 패키지, 설정·환경 도구, 프로브 반복 실행·집계 |
| `configs/` | 버전 관리하는 실험 설정 예시 |
| `benchmarks/` | 평가 절차와 데이터셋 명세; 실행 산출물은 `runs/`에 보관하고 Git 제외 |
| `scripts/` | Host → RP5 소스 배포 등 운영 도구 |
| `tests/` | 모델·오디오 없이 호스트에서 수행하는 검사 |
| `docs/` | 설치, 아키텍처, 벤치마크 정의, 배포 절차 |
| `experiments/` | 모델별 탐색 실험, 원본 및 작은 결과 요약 |
| `models/`, `data/` | 로컬 가중치·오디오; 안내 문서만 버전 관리 |
| `results/` | 로컬 실행 결과; `summaries/`의 작은 MD/JSON/CSV만 검토 후 버전 관리 가능 |

## Host에서 시작

저장소 루트에서 실행합니다. Windows Host는 WSL/Bash를 사용하면 아래 배포 스크립트를 그대로 사용할 수 있습니다.

```bash
cd /d/shin/edge-asr
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
edge-asr env
edge-asr check-config configs/zipformer_streaming.toml
python -m unittest discover -s tests -v
sha256sum -c experiments/m1_zipformer/SHA256SUMS
```

기본 설치는 외부 런타임 의존성이 없습니다. `check-config`는 설정 구조만 확인하며 모델 파일, ARM64 호환성 또는 추론 성공을 확인하지 않습니다. `python -m edge_asr`도 같은 명령을 제공합니다. 기존 RP5 가상환경으로 반복 도구를 사용할 때는 추가 설치가 필요하지 않습니다. 새 비교 환경에 선택 의존성 `.[zipformer]`를 설치하는 방법은 [환경 설치](docs/setup.md)에 있습니다.

## 기존 Zipformer 실험

아래 모든 값은 **동일한 3.526초 WAV 단일 샘플에 대한 설정별 1회 측정**입니다. 반복 측정 통계나 모델 전체의 성능을 의미하지 않습니다. 기존 RP5에서 가져온 파일은 수정하지 않고 보존했습니다.

| 측정 모드 | 스레드 | 결과 | 출처 |
| --- | --- | --- | --- |
| 파일 일괄 처리 | 미상 | RTF 0.110 | 사용자 제공 기존 측정값; 원시 로그 없음 |
| 오디오 속도 모사 스트리밍 | 2 | 오디오 시작 기준 첫 부분 출력 0.837초; 최종 처리 지연 0.064초 | 보존된 원시 로그 |

0.837초에는 오디오가 입력될 때까지의 대기가 포함됩니다. 이를 실제 인식 처리 지연으로 해석하면 안 됩니다. 0.064초는 파일 끝 이후 결과 확정까지의 프로브 측정값이며 실제 발화 끝의 endpoint 지연은 아닙니다. 파일 일괄 처리 RTF와 스트리밍 전체 경과 시간은 서로 다른 측정입니다.

1·2·4스레드 원시 결과, 측정 한계 및 무결성 해시는 [실험 README](experiments/m1_zipformer/README.md), 지표 정의는 [벤치마크 설계](docs/benchmark_design.md)를 참고하세요.

## RP5 배포와 다음 구현

```bash
# RP5: 기존 모델·가상환경이 있는 edge-asr는 유지하고 별도 clone
cd /home/deepet/projects &&
git clone https://github.com/shinjuyeop/edge-asr.git edge-asr-git &&

# 기존 Python 3.11 환경·모델로 새 clone의 반복 도구 실행
/home/deepet/projects/edge-asr/.venv/bin/python \
  /home/deepet/projects/edge-asr-git/scripts/benchmark_zipformer.py \
  --asset-root /home/deepet/projects/edge-asr
```

위 명령은 최초 clone용이며 clone이 실패하면 추론을 실행하지 않습니다. `edge-asr-git`이 이미 있으면 삭제하거나 다시 clone하지 말고 [기존 clone 확인·업데이트 절차](docs/deployment.md)를 따릅니다. 이미 벤치마크가 실행 중이면 종료 전까지 pull·재실행하지 않습니다.

기본 흐름은 Host에서 개발·커밋·push하고 RP5의 별도 clone에서 `git pull --ff-only origin main`으로 가져오는 방식입니다. 소스 커밋은 함께 관리하고 가중치·WAV·가상환경·원시 결과는 각 장비에 둡니다. 공개 저장소의 clone/pull은 GitHub 로그인 없이 사용할 수 있습니다. 커밋 일치 확인, 기존 RP5 보존 및 rsync 대안은 [배포](docs/deployment.md)를 따릅니다. 위 RP5 실행 명령은 아직 실제 검증하지 않았습니다.

다음 순서는 기존 RP5 가상환경 import 확인 및 반복 도구 실행 → 상주 Zipformer 어댑터·공통 실행기 → 평가용 정답 전사·CER → 청크 지연·자원·백로그 계측 → SenseVoice/Qwen3-ASR 비교 → 마이크·서비스 통합입니다. 설계 범위와 확장 방침은 [아키텍처](docs/architecture.md)에 정리했습니다.
