# edge-asr

Raspberry Pi 5(ARM64, Debian 13, RAM 8GB)에서 한국어 ASR 모델을 비교하고 실시간 배포하기 위한 연구개발 저장소입니다. Host PC에서 개발·단위 검사를 수행하고, 실제 추론·벤치마크는 RP5에서 수행합니다. 기준 인터프리터는 Python 3.11입니다.

초기 골격, 패키징, 설정 검사 CLI, 기존 프로브의 반복 실행·집계 도구, 벤치마크 설계, 배포 절차 및 실험 원본 보존을 제공합니다. 상주 모델을 사용하는 공통 추론 실행기, 모델 어댑터, 마이크 입력, 서비스 배포는 아직 구현하지 않았습니다. RP5 직접 접속·실행 검증과 사용자가 전달한 실행 결과는 구분해 기록합니다.

초기 구성의 실제 로컬 검사 결과는 [검증 기록](docs/verification.md)에 있습니다.

2026-10-28 사용자가 전달한 RP5 반복 요약에서는 동일 WAV를 스레드별 30회 측정해 총 90/90회 성공했고 워밍업 9회도 실패가 없었습니다. 후속 [RP5 Codex 검사 보고](results/summaries/rp5-consolidation-audit-20261028.json)에서 한 폴더 통합, 자산·기록 일치 및 RP5 단위 테스트 35개 통과가 확인됐다고 보고됐습니다. 실측 환경은 Python 3.11.18 / NumPy 2.4.6 / sherpa-onnx 2.0.1이고, [이번 조건의 잠정 기준값](results/summaries/zipformer-repeat-20261028-reported.md)은 2스레드입니다. 이 기록은 사용자가 전달한 검사 보고에 근거하며 Host 에이전트의 직접 원격 실행이나 원본 JSON 전송을 뜻하지 않습니다.

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

원시 실행 결과는 Git에서 제외하고 현재 브랜치에는 작은 요약만 유지합니다. 초기 로그 3개는 `archive-initial-zipformer-20261028` 태그에 보관했습니다. 프로브·모델·가상환경·2스레드 기준선은 이 정리로 바뀌지 않습니다.

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

1·2·4스레드 요약과 보관 태그의 원시 결과 위치, 측정 한계 및 무결성 확인 방법은 [실험 README](experiments/m1_zipformer/README.md), 지표 정의는 [벤치마크 설계](docs/benchmark_design.md)를 참고하세요.

위 기존 1회 측정과 별도로, [2026-10-28 반복 요약](results/summaries/zipformer-repeat-20261028-reported.md)에서 2스레드의 첫 부분 시점 p50/p95는 0.831/0.835초, 최종 지연 p50/p95는 0.064/0.086초로 보고됐습니다. 같은 WAV 한 개의 반복이며 여러 발화에서의 정확도·실시간 서비스 검증은 아닙니다.

## RP5 배포와 다음 구현

RP5 작업 폴더는 **`/home/deepet/projects/edge-asr` 하나**로 통일합니다. 소스·`.git`·기존 `.venv`·모델·데이터·실행 결과를 이 안에 두고, Git에는 소스와 작은 요약만 올립니다.

현재 RP5는 사용자 전달 검사 보고 기준으로 통합 완료입니다. 아래는 아직 `edge-asr`, `edge-asr-git`, `edge-asr-bench-20261028-01` 세 폴더를 사용하는 경우에만 수행하는 일회성 절차입니다. 이미 통합한 장비에서는 재실행하지 않습니다.

```bash
cd /home/deepet/projects/edge-asr-git &&
git pull --ff-only origin main &&
/home/deepet/projects/edge-asr/.venv/bin/python scripts/consolidate_rp5.py --apply &&
cd /home/deepet/projects/edge-asr
```

통합 도구는 기존 `edge-asr` 경로를 유지합니다. 다른 두 폴더는 그 안의 `.local-backups/consolidate-<시각>-<임의문자>/`에 보관하므로 프로젝트 부모 아래에는 작업 폴더 하나만 남습니다. 모델·가상환경을 옮기거나 다시 설치하지 않으며 기존 소스 충돌은 백업 후 반영합니다. 현재 RP5에서는 `0297f0a` 커밋 기준 추적 파일 39개가 소스 백업과 일치하고 통합 영수증이 `completed`라고 보고됐습니다. 미리보기·거부 조건·복원 기록은 [배포 안내](docs/deployment.md)를 따릅니다.

통합 후 평소 사용법은 다음과 같습니다. 벤치마크는 새 측정이 필요할 때만 실행합니다.

```bash
cd /home/deepet/projects/edge-asr &&
git pull --ff-only origin main

# 새 측정을 할 때만 실행
.venv/bin/python scripts/benchmark_zipformer.py --asset-root .
```

Host에서 개발·커밋·push하고 RP5의 같은 프로젝트 저장소에서 pull·실행합니다. 가중치·WAV·가상환경·원시 결과·내부 백업은 각 장비에 둡니다. 기존 90회 결과를 통합 때문에 다시 측정할 필요는 없습니다.

현재 단계는 사용자 전달 RP5 검사 보고로 환경·통합·기존 반복 기준선을 정리한 상태입니다. 다음 개발 순서는 상주 Zipformer 어댑터·공통 실행기 → 여러 발화의 정답 전사·CER → 청크 지연·자원·백로그 계측 → SenseVoice/Qwen3-ASR 비교 → 마이크·서비스 통합입니다. 원시 파일은 RP5 백업에 유지하고 필요할 때 상세 검증에 사용합니다. 설계 범위는 [아키텍처](docs/architecture.md)에 정리했습니다.
