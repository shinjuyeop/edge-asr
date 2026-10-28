# GitHub로 Host와 RP5 소스 관리

공통 원격 저장소는 [shinjuyeop/edge-asr](https://github.com/shinjuyeop/edge-asr)입니다. Host와 RP5는 같은 저장소의 **서로 다른 로컬 복제본**을 사용합니다. Host의 작업 폴더가 RP5와 실시간 공유되는 것은 아닙니다. 커밋·push한 소스만 RP5에서 fetch/pull할 수 있습니다.

## 관리 범위

| 대상 | 관리 방식 |
| --- | --- |
| 소스·설정·문서·테스트 | Host에서 개발·검사·커밋·push, RP5에서 clone/pull |
| 보존된 기존 프로브·작은 원시 실험 결과 | Git 버전 관리. 원본은 수정하지 않음 |
| 가중치·WAV·가상환경·캐시 | 각 장비에 로컬 보관. Git으로 복사하지 않음 |
| 새 원시 벤치마크 결과 | RP5의 `benchmarks/runs/` 등에 기록. Git 제외 |
| 검토한 작은 요약 | Host로 회수 후 `results/summaries/`에서 커밋 가능 |

RP5에서 실제 추론·성능을 측정합니다. Host의 검사 결과나 GitHub push 성공을 RP5 검증 성공으로 기록하지 않습니다.

## Host의 개발 흐름

현재 Host 경로는 `/d/shin/edge-asr`입니다. 최초 공개 저장소 연결 후 `origin`을 사용합니다.

```bash
cd /d/shin/edge-asr
git remote -v
git status --short
.venv/bin/python -m unittest discover -s tests -v
sha256sum -c experiments/m1_zipformer/SHA256SUMS

# 아래 README.md는 변경 파일 선택 예시. 실제 수정 파일을 검토해 추가.
git add README.md
git diff --cached --stat
git commit -m "docs: update project guide"
git push origin main
git rev-parse HEAD
```

아직 커밋하지 않은 파일은 push되지 않습니다. 초기 커밋 이후 기능별 브랜치를 사용할 수 있지만 지금은 Host에서 작성하고 RP5에서는 소스를 수정하지 않는 단순한 흐름으로 시작합니다.

## RP5 최초 clone

기존 `/home/deepet/projects/edge-asr`에는 모델·가상환경·과거 실험이 있으므로 그대로 둡니다. 새 복제본은 별도의 `/home/deepet/projects/edge-asr-git`에 만듭니다. 해당 이름이 이미 있으면 내용을 확인하고 다른 새 경로를 선택합니다.

```bash
# RP5 터미널에서 실행
git --version
cd /home/deepet/projects
git clone https://github.com/shinjuyeop/edge-asr.git edge-asr-git
cd edge-asr-git
git rev-parse HEAD
sha256sum -c experiments/m1_zipformer/SHA256SUMS
```

공개 저장소의 HTTPS clone/pull에는 GitHub 로그인이나 RP5의 GitHub SSH 키가 필요하지 않습니다. RP5에서 GitHub에 도달할 수 있는 네트워크와 git은 필요합니다. Host → RP5 SSH 연결은 이 소스 배포 방식의 필수 조건이 아닙니다. 기존 RP5 `.git`을 초기화하거나 remote를 변경하지 않습니다.

## 기존 환경·자산으로 반복 측정

새 clone의 도구를 기존 가상환경으로 실행하고 자산 루트는 기존 프로젝트를 지정합니다. 설치·가상환경 재생성·모델 복사가 필요하지 않습니다. 실제 import와 추론 성공은 도구가 검사합니다.

```bash
/home/deepet/projects/edge-asr/.venv/bin/python \
  /home/deepet/projects/edge-asr-git/scripts/benchmark_zipformer.py \
  --asset-root /home/deepet/projects/edge-asr
```

새 결과는 `edge-asr-git/benchmarks/runs/` 아래의 고유 실행 폴더에 생성되며 Git에서 제외됩니다. [반복 벤치마크 안내](rp5_repeat_benchmark.md)의 측정 범위와 실패 처리 방침을 따릅니다. 새로운 Python 환경에서 후보 의존성을 비교할 때만 [설치 안내](setup.md)에 따라 별도 `.venv`를 준비합니다.

## 이후 업데이트

먼저 Host에서 검사·커밋·push합니다. RP5에서 실행 중인 벤치마크가 끝난 후 아래를 실행합니다.

```bash
cd /home/deepet/projects/edge-asr-git
git status --short
git pull --ff-only origin main
git rev-parse HEAD
sha256sum -c experiments/m1_zipformer/SHA256SUMS
```

Host와 RP5의 `git rev-parse HEAD` 값이 같으면 같은 소스 커밋입니다. Python 패키지·모델·OS 환경까지 같다는 뜻은 아니므로 벤치마크 환경 기록은 따로 남깁니다. Git은 `.venv`나 모델 파일을 업데이트하지 않습니다. 도구의 의존성이 바뀌면 새 환경에서 별도 검증합니다.

RP5에서 수정한 소스가 있거나 브랜치가 갈라져 pull이 실패하면 로컬 변경과 커밋을 확인합니다. 원인을 해결하기 위해 `reset --hard`, `clean`, 강제 push를 사용하지 않습니다. 특정 커밋을 기준으로 고정 비교할 때는 깨끗한 복제본에서 `git checkout --detach <검증할-커밋-SHA>`를 사용하고 실행 manifest의 커밋을 확인합니다. 실행 도중에는 pull·checkout을 하지 않습니다.

## 결과 회수

원시 결과는 RP5 로컬에 보관합니다. 파일 전송이 필요하면 이미 준비된 SSH/SFTP 경로로 회수할 수 있습니다. 예를 들어 Host에서 다음 명령을 실행합니다.

```bash
mkdir -p results/rp5-repeat-20261028-01
rsync -rz --ignore-existing \
  rp5:/home/deepet/projects/edge-asr-git/benchmarks/runs/ \
  results/rp5-repeat-20261028-01/
```

`rp5`는 Host SSH 설정의 별칭이며 파일 전송에는 별도 SSH 인증이 필요합니다. 현재 이 작업 환경의 RP5 SSH 키 인증은 거절되었고 원격 배포·추론은 아직 수행하지 못했습니다. GitHub clone/pull과 Host → RP5 SSH 인증은 각각 별개입니다. 작은 `summary.md`/`summary.json`은 사용자가 RP5 터미널에서 내용을 전달해도 됩니다.

## rsync 대안

GitHub를 사용할 수 없을 때 기존 `scripts/deploy_rp5.sh`를 사용할 수 있습니다. 기본 dry-run이며 `--apply`에서 복사하고, 기존 원격 파일을 덮어쓰거나 삭제하지 않습니다. Git clone에 이 스크립트를 겹쳐 사용하지 말고 별도 새 소스 폴더를 지정합니다.

```bash
cd /d/shin/edge-asr
bash scripts/deploy_rp5.sh rp5 /home/deepet/projects/edge-asr-source-copy-001
bash scripts/deploy_rp5.sh rp5 /home/deepet/projects/edge-asr-source-copy-001 --apply
```

이 대안은 `.git`을 전송하지 않아 `git pull`할 수 없습니다. 동일 경로 업데이트도 지원하지 않으므로 변경 배포마다 새 경로를 사용합니다. 서비스 등록·재시작과 마이크 실시간 배포는 현재 구현 범위가 아닙니다.
