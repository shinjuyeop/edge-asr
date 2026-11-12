# RP5 소스 동기화와 실행

공통 저장소는 [shinjuyeop/edge-asr](https://github.com/shinjuyeop/edge-asr)입니다. 현재는 **Git 소스 동기화 + 장비별 가상환경 + 별도 모델/native 자산**으로 실행합니다. Docker·서비스 자동 등록·마이크 입력 배포는 구현하지 않았습니다. [환경 기록](../reference/environment.md), [개발 절차](../../CONTRIBUTING.md)를 함께 확인합니다.

| 장비 | 작업 경로 | 역할 |
| --- | --- | --- |
| Host | `/d/shin/edge-asr` | 구현·테스트·main 반영·push |
| RP5 | `/home/deepet/projects/edge-asr` | main 동기화·실제 추론·벤치마크 |

모델·음원·가상환경·외부 엔진은 같은 작업 폴더 안에 두되 Git에서 제외합니다. 기존 환경·자산은 그대로 사용합니다. 과거 다중 폴더 통합은 완료했으므로 [일회성 통합 절차](../archive/consolidation.md)를 반복하지 않습니다.

## 업데이트 순서

1. Host에서 작업 브랜치의 변경과 [Host 검사](../../tests/README.md)를 마칩니다.
2. 검사 후 main에 반영·push하고 커밋을 확인합니다.
3. RP5에서 실행 중인 실험이 없을 때 아래 명령으로 소스만 갱신합니다.
4. 추론 동작을 바꾼 경우 필요한 실제 추론을 새 실행 ID로 수행합니다. 문서 수정만으로 모델 추론을 반복하지 않습니다.

```bash
# Host: 현재 소스 확인
git status --short
git rev-parse HEAD

# RP5: 실행 중인 실험이 없을 때만 수행
cd /home/deepet/projects/edge-asr &&
git pull --ff-only origin main &&
git rev-parse HEAD &&
sha256sum -c experiments/m1_zipformer/SHA256SUMS
```

Host와 RP5의 HEAD가 같아도 OS·패키지·자산·native 빌드가 같은 것은 아닙니다. Git pull은 Python 환경·모델을 갱신하지 않습니다. 실행 중에는 pull·checkout·패키지 변경을 하지 않습니다.

현재 Qwen의 설정·명령·평가 순서는 [실행 안내](qwen3_optimization.md#현재-개발용-대표-명령)를 사용합니다. `scripts/benchmark_zipformer.py`와 `tools repeat-zipformer`는 과거 M1 재현용입니다.

## SSH와 자산·결과 전달

현재 Host는 `rp5` SSH 별칭으로 읽기·원격 실행·결과 회수가 가능합니다. SSH 접속 설정은 장비별로 준비합니다. 개인 키·비밀번호·개인 접속 설정은 저장소에 넣지 않습니다.

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 rp5 \
  'cd /home/deepet/projects/edge-asr && .venv/bin/python scripts/edge_asr.py env'
```

Git은 소스·설정·문서·검토한 요약을 전달합니다. 모델·음원·원시 결과는 필요한 실행 폴더만 SSH/rsync로 전달합니다. 예를 들어 Host에서 **새 회수 경로**를 정하고 다음 형식으로 실행합니다.

```bash
mkdir -p "results/optimization/opt3/<새회수ID>"
rsync -rz --ignore-existing \
  "rp5:/home/deepet/projects/edge-asr/results/optimization/opt3/<실행ID>/" \
  "results/optimization/opt3/<새회수ID>/"
```

`<...>`는 실제 경로로 바꿉니다. `--ignore-existing`는 다른 내용의 기존 파일도 교체하지 않으므로 회수 후 실행 영수증·해시를 대조합니다. 데이터 준비·전달은 [데이터 안내](datasets.md), 고정100개는 [준비·검증 안내](../research/fixed100.md)를 따릅니다.

## GitHub 인증과 새 장비

RP5 origin의 SSH 인증을 사용할 수 없을 때 공개 HTTPS로 소스만 받을 수 있습니다. origin·키·환경 설정은 바꾸지 않습니다.

```bash
GIT_TERMINAL_PROMPT=0 git fetch --no-tags \
  https://github.com/shinjuyeop/edge-asr.git main:refs/remotes/origin/main &&
git merge --ff-only origin/main
```

새 장비에는 새 프로젝트 폴더에 clone하고 [환경 준비](setup.md)를 진행합니다. 기존 RP5에는 clone이나 환경 초기화를 반복하지 않습니다. 과거 원시 `benchmarks/runs/`와 `.local-backups/`는 보존합니다.

`scripts/deploy_rp5.sh`는 과거 rsync 배포 재현용입니다. 현재 업데이트 절차는 위 Git 동기화이며, 이 스크립트로 새로운 운영 폴더를 계속 만들지 않습니다.
