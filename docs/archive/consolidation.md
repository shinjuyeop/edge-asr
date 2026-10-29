# 이전 세 폴더의 일회성 통합

현재 RP5는 통합 완료 상태입니다. 새로 같은 이전 구조를 만난 경우에만 사용하는 유지보수 절차입니다. [현재 배포 안내](../guides/deployment.md)를 먼저 확인하세요.

## 기존 세 폴더를 한 번만 통합

이전에 rsync 배포 후 GitHub 방식으로 전환하여 `edge-asr`, `edge-asr-git`, `edge-asr-bench-20261028-01`이 생겼습니다. `scripts/edge_asr.py tools consolidate`는 기존 `edge-asr` 경로를 유지한 채 GitHub 소스를 합치고, 나머지 두 폴더 전체를 그 안의 Git 제외 백업 폴더에 보관합니다.

현재 RP5는 사용자 전달 [Codex 검사 보고](../../results/summaries/rp5-consolidation-audit-20261028.json) 기준으로 통합 완료입니다. `0297f0a`에서 영수증 `completed`, 소스 백업과 추적 파일 39개 일치, 기존 두 폴더의 내부 백업 이동이 확인됐다고 보고됐습니다. Host 에이전트가 원격 실행한 결과는 아니며 원본 영수증 파일을 Host로 전송한 것도 아닙니다. 이미 통합한 현재 장비에서는 아래 일회성 통합 명령을 다시 실행하지 않습니다.

아직 이전 세 폴더를 사용 중인 장비에서는 실행 중인 벤치마크와 Git 작업을 먼저 종료하고 통합 중에는 소스·폴더를 편집하지 않습니다.

```bash
# RP5: 기존 clone에서 최신 통합 도구 받기
cd /home/deepet/projects/edge-asr-git &&
git pull --ff-only origin main

# 선택: 실제 복사·이동이 없는 미리보기
/home/deepet/projects/edge-asr/.venv/bin/python scripts/edge_asr.py tools consolidate
```

실제 적용은 다음 명령으로 수행합니다. 기본 경로는 로그인 사용자의 `~/projects` 아래입니다.

```bash
cd /home/deepet/projects/edge-asr-git &&
git pull --ff-only origin main &&
/home/deepet/projects/edge-asr/.venv/bin/python scripts/edge_asr.py tools consolidate --apply &&
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
