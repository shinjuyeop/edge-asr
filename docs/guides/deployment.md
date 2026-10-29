# Host와 RP5를 같은 GitHub 저장소로 관리

공통 저장소는 [shinjuyeop/edge-asr](https://github.com/shinjuyeop/edge-asr)입니다. Host와 RP5는 서로 다른 컴퓨터의 로컬 복제본이며 각 컴퓨터에서 작업 폴더는 하나만 사용합니다.

| 장비 | 작업 경로 | 역할 |
| --- | --- | --- |
| Host | `/d/shin/edge-asr` | 구현·테스트·커밋·push |
| RP5 | `/home/deepet/projects/edge-asr` | pull·실제 추론·벤치마크 |

## RP5 최종 구조

```text
/home/deepet/projects/edge-asr/
├── .git/                 # GitHub 소스 이력
├── .venv/                # 기존 Python 3.11 환경 (Git 제외)
├── models/               # 기존 모델·테스트 WAV (안내 문서 외 Git 제외)
├── data/                 # 평가 오디오·정답 (안내 문서 외 Git 제외)
├── src/edge_asr/
├── scripts/
├── configs/
├── benchmarks/runs/      # 새 원시 실행 결과 (Git 제외)
├── results/summaries/    # 검토한 작은 요약만 버전 관리
└── .local-backups/       # 통합 전 소스·과거 결과·Git 메타데이터 보관 (Git 제외)
```

가중치·오디오·가상환경을 Git에 올리지 않는 것과 파일을 다른 프로젝트 폴더에 보관하는 것은 별개입니다. 한 작업 폴더 안에 함께 두고 Git 제외 규칙으로 구분합니다.

과거 세 폴더를 아직 사용하는 장비만 [일회성 통합 절차](../archive/consolidation.md)를 따릅니다. 현재 RP5에서는 반복하지 않습니다.

## Host의 평소 개발

2026-10-28 Host의 `rp5` SSH 별칭으로 키 인증·원격 명령 실행·결과 회수를 직접 확인했습니다. Host와 RP5가 켜져 있고 SSH 네트워크가 연결되는 동안 Host에서 코드 개발, Git push, RP5 pull·추론 실행, 결과 회수·분석을 진행합니다. 실제 추론 연산은 RP5에서 수행합니다. SSH 설정·개인 키·비밀번호는 저장소에 넣지 않습니다.

```bash
# Host에서 RP5의 현재 환경 확인
ssh -o BatchMode=yes -o ConnectTimeout=10 rp5 \
  'cd /home/deepet/projects/edge-asr && .venv/bin/python scripts/edge_asr.py env'
```

여러 발화의 준비·전달·실행은 [데이터 안내](datasets.md)를 따릅니다. Git은 소스와 작은 요약을 전달하고, Git 제외 음원·원시 결과는 필요한 폴더만 SSH/rsync로 전달합니다. 새 평가마다 고유 결과 폴더를 사용하며 원본·기존 실행을 덮어쓰지 않습니다.

```bash
cd /d/shin/edge-asr
git status --short
.venv/bin/python -m unittest discover -s tests -v
sha256sum -c experiments/m1_zipformer/SHA256SUMS

# 실제 변경 파일을 검토해 선택. 아래 README.md는 예시.
git add README.md
git diff --cached --stat
git commit -m "docs: update project guide"
git push origin main
git rev-parse HEAD
```

아직 커밋하지 않은 파일은 push되지 않습니다. 소스·설정·문서·테스트·프로브와 검토한 작은 요약을 버전 관리합니다. 원시 결과·자산·백업은 제외합니다. 초기 로그 3개는 `archive-initial-zipformer-20261028` 태그에 보관하고 현재 main의 추적 예외는 없앴습니다.

## RP5의 평소 업데이트·실행

```bash
cd /home/deepet/projects/edge-asr &&
git pull --ff-only origin main &&
git rev-parse HEAD &&
sha256sum -c experiments/m1_zipformer/SHA256SUMS
```

새 벤치마크가 필요한 경우에만 같은 폴더에서 실행합니다.

```bash
cd /home/deepet/projects/edge-asr &&
.venv/bin/python scripts/benchmark_zipformer.py --asset-root .
```

기존 성공 결과를 통합 때문에 다시 측정할 필요는 없습니다. Git pull은 가상환경·모델을 업데이트하지 않습니다. 실행 중에는 pull·checkout을 하지 않습니다. Host와 RP5의 HEAD가 같으면 같은 소스 커밋이며 OS·라이브러리·자산까지 같다는 뜻은 아니므로 실행 환경도 따로 기록합니다.

## 과거 결과와 회수

과거 `edge-asr-bench-20261028-01/benchmarks/runs/`는 통합 기록에 출력된 `.local-backups/.../edge-asr-bench-20261028-01/benchmarks/runs/`에서 찾습니다. 그 안의 manifest에 남은 과거 경로는 당시 출처이며 새 위치로 고치지 않습니다. 새 실행은 현재 `benchmarks/runs/`에 저장됩니다.

원시 결과 회수는 필요한 실행 폴더만 SSH/SFTP로 가져옵니다. 예를 들어 Host에서는 다음과 같습니다.

```bash
mkdir -p results/rp5-new-runs
rsync -rz --ignore-existing \
  rp5:/home/deepet/projects/edge-asr/benchmarks/runs/ \
  results/rp5-new-runs/
```

`rp5`는 Host SSH 설정의 별칭이며 회수에는 별도 SSH 인증이 필요합니다. 공개 GitHub pull에는 GitHub 로그인·RP5 SSH 키가 필요하지 않습니다. 작은 요약을 터미널에서 전달해도 됩니다.

## 새 RP5에서 처음 시작할 때

프로젝트 폴더가 없는 새 장비에서는 `git clone https://github.com/shinjuyeop/edge-asr.git /home/deepet/projects/edge-asr`로 처음부터 한 경로에 만듭니다. 그 안에서 Python 3.11 환경·자산을 준비합니다. 이미 기존 폴더가 있는 현재 RP5에는 이 clone 명령을 사용하지 않습니다.

`scripts/deploy_rp5.sh`는 과거 rsync 배포 재현용으로 남겨 둡니다. 현재 운영 경로의 업데이트는 Git pull을 사용하며 새 배포 폴더를 계속 만들지 않습니다. 서비스 등록·마이크 실시간 배포는 아직 구현하지 않았습니다.
