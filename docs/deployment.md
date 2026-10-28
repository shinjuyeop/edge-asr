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

## 기존 세 폴더를 한 번만 통합

이전에 rsync 배포 후 GitHub 방식으로 전환하여 `edge-asr`, `edge-asr-git`, `edge-asr-bench-20261028-01`이 생겼습니다. `scripts/consolidate_rp5.py`는 기존 `edge-asr` 경로를 유지한 채 GitHub 소스를 합치고, 나머지 두 폴더 전체를 그 안의 Git 제외 백업 폴더에 보관합니다.

현재 RP5는 사용자 전달 [Codex 검사 보고](../results/summaries/rp5-consolidation-audit-20261028.json) 기준으로 통합 완료입니다. `0297f0a`에서 영수증 `completed`, 소스 백업과 추적 파일 39개 일치, 기존 두 폴더의 내부 백업 이동이 확인됐다고 보고됐습니다. Host 에이전트가 원격 실행한 결과는 아니며 원본 영수증 파일을 Host로 전송한 것도 아닙니다. 이미 통합한 현재 장비에서는 아래 일회성 통합 명령을 다시 실행하지 않습니다.

아직 이전 세 폴더를 사용 중인 장비에서는 실행 중인 벤치마크와 Git 작업을 먼저 종료하고 통합 중에는 소스·폴더를 편집하지 않습니다.

```bash
# RP5: 기존 clone에서 최신 통합 도구 받기
cd /home/deepet/projects/edge-asr-git &&
git pull --ff-only origin main

# 선택: 실제 복사·이동이 없는 미리보기
/home/deepet/projects/edge-asr/.venv/bin/python scripts/consolidate_rp5.py
```

실제 적용은 다음 명령으로 수행합니다. 기본 경로는 로그인 사용자의 `~/projects` 아래입니다.

```bash
cd /home/deepet/projects/edge-asr-git &&
git pull --ff-only origin main &&
/home/deepet/projects/edge-asr/.venv/bin/python scripts/consolidate_rp5.py --apply &&
cd /home/deepet/projects/edge-asr
```

통합 시 동작은 다음과 같습니다.

1. 같은 GitHub 저장소의 깨끗한 main clone인지, HEAD가 origin/main과 같은지 검사합니다. 대상의 가상환경·모델 위치, 경로 충돌·symlink·Git lock·실행 중인 벤치마크도 검사합니다.
2. 새 고유 `.local-backups/consolidate-<UTC시각>-<임의문자>/`를 만듭니다. 덮어쓸 기존 소스는 `original-files/`에 먼저 복사합니다. 원본 파일이 hard link여도 다른 링크의 내용을 바꾸지 않도록 새 파일로 교체합니다.
3. clone의 추적 소스와 Git 메타데이터를 기존 `edge-asr`에 반영합니다. 기존 대상 `.git`이 있으면 같은 이력의 조상 또는 초기화만 된 저장소일 때만 허용하고 `original.git/`에 보존합니다. 다른 저장소·미포함 커밋은 자동 처리하지 않습니다.
4. 기존 clone과 과거 배포본 전체를 내부 백업으로 이동합니다. 예전 벤치마크 원시 결과도 그 안에 그대로 남습니다. `.venv`·가중치·WAV 경로와 내용은 유지하며 재설치·재측정하지 않습니다.
5. `receipt.json`에 적용 상태, 커밋, 파일 백업 목록과 옛 경로 → 새 보관 경로를 기록합니다. 실패·Ctrl-C 시 되돌리기를 시도하고 성공한 복원은 `rolled_back`으로 표시합니다. 전원 차단·복원 중 I/O 오류 등에서는 자동 복원이 보장되지 않으므로 보관 자료와 기록을 유지합니다.

수정 중인 clone, 별도 `.venv`가 있는 다른 폴더, Git worktree/외부 object 의존성, 별도 파일시스템·예상 밖 링크 구조는 자동으로 통합하지 않습니다. 이유가 출력되면 그 출력을 확인합니다. 기존 자료를 삭제하거나 강제 reset하여 우회하지 않습니다. `--source`, `--target`, `--legacy-dir`로 경로를 명시할 수 있지만 현재 안내된 기본값이면 충분합니다.

통합이 끝나면 `edge-asr-git`과 과거 배포 폴더는 부모 경로에서 내부 백업으로 이동한 상태입니다. 다시 clone하거나 통합 도구를 재실행할 필요가 없습니다. 이전 경로에 머물던 다른 터미널도 `cd /home/deepet/projects/edge-asr`로 이동하세요.

## Host의 평소 개발

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

아직 커밋하지 않은 파일은 push되지 않습니다. 소스·설정·문서·테스트·보존 실험과 검토한 작은 요약을 버전 관리합니다. 새 원시 결과·자산·백업은 제외합니다. 작은 초기 보존 로그 `experiments/m1_zipformer/results/streaming_threads_{1,2,4}.txt` 3개는 의도적인 추적 예외입니다.

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
