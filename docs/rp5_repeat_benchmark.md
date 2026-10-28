# RP5 Zipformer 반복 벤치마크 실행

기존 단일 샘플·스레드별 1회 실험을 넘어 반복 변동성을 측정하는 단계입니다. Host에서 준비한 도구가 보존된 원본 프로브를 별도 자식 프로세스로 실행합니다. 원본 파일과 RP5 기존 가상환경·모델은 수정하지 않습니다. 패키지 설치도 필요하지 않습니다.

## 확인된 경로와 미검증 항목

2026-10-28 사용자가 전달한 RP5 터미널 출력에서 aarch64 / Debian 13.3, `/home/deepet/.local/bin/python3.11`, `/home/deepet/projects/edge-asr/.venv/pyvenv.cfg`의 Python 3.11 설정과 모델·WAV 파일 목록을 확인했습니다. 당시 기본 `python`은 시스템 3.13.5였습니다.

기존 `.venv`의 실행·NumPy/sherpa-onnx import와 이번 반복 실행은 아직 검증하지 않았습니다. 도구는 시작 시 지정한 Python의 버전·import를 검사하고 실패하면 환경 검사 로그를 남기고 종료합니다. 대상 Python의 심볼릭 링크를 풀지 않아 기존 가상환경을 유지합니다.

## 1. Host에서 push, RP5에서 clone/pull

공통 저장소는 [shinjuyeop/edge-asr](https://github.com/shinjuyeop/edge-asr)입니다. Host에서 검사한 소스를 커밋·push한 뒤, RP5 터미널에서 아래를 실행합니다. 기존 `/home/deepet/projects/edge-asr`는 보존하고 별도 clone을 만듭니다. 새 경로가 이미 있으면 기존 clone인지 확인하고 다른 새 경로를 선택하거나 업데이트 절차를 따릅니다.

```bash
cd /home/deepet/projects &&
git clone https://github.com/shinjuyeop/edge-asr.git edge-asr-git &&
cd edge-asr-git &&
git rev-parse HEAD &&
sha256sum -c experiments/m1_zipformer/SHA256SUMS
```

이후에는 벤치마크 종료 후 `git pull --ff-only origin main`으로 업데이트합니다. Host와 RP5의 커밋 SHA를 비교하고, 실행 중에는 소스를 변경하지 않습니다. 공개 HTTPS clone/pull에는 Host → RP5 SSH 연결이나 RP5의 GitHub 인증이 필요하지 않습니다. `.venv`·모델·WAV는 Git으로 전송하지 않습니다. clone의 Git 커밋과 도구·프로브 SHA-256은 실행 기록에 남습니다. [배포 안내](deployment.md)에 일상적인 개발·업데이트 흐름과 rsync 대안을 정리했습니다.

이미 폴더가 있다는 clone 오류가 나면 [기존 clone 확인 절차](deployment.md)를 따릅니다. 이미 워밍업이 시작되었다면 새 측정을 겹쳐 실행하지 말고, 소스 위치·커밋을 읽기 전용으로 확인한 뒤 진행 중인 실행을 사용합니다. clone 오류를 무시하고 다음 실행 블록을 새로 시작하지 않습니다.

## 2. RP5에서 반복 실행

RP5 터미널에서 아래 명령을 한 번 실행합니다. 기존 가상환경의 Python을 절대 경로로 사용하고, 기존 프로젝트 루트는 원본 프로브의 상대 모델 경로를 해석하는 작업 디렉터리로만 사용합니다.

```bash
/home/deepet/projects/edge-asr/.venv/bin/python \
  /home/deepet/projects/edge-asr-git/scripts/benchmark_zipformer.py \
  --asset-root /home/deepet/projects/edge-asr
```

기본값은 1·2·4스레드 각각 별도 워밍업 3회와 측정 30회, 총 99회 프로브 실행입니다. 매 라운드 순서는 `1,2,4` → `2,4,1` → `4,1,2`로 교차합니다. 측정에는 원본 WAV 길이만큼의 입력 대기가 매번 포함되어 수분 이상 걸릴 수 있습니다. `--warmups`, `--repetitions`, `--threads`, `--timeout-s`로 조건을 변경할 수 있으며 실제 인자가 기록됩니다. 기존 TOML은 이 도구에 적용되지 않습니다.

모든 실행은 직렬입니다. 1회 전체 프로세스 제한 시간은 기본 120초입니다. 워밍업 실패는 즉시 중단하고, 측정 단계 실패는 기록한 뒤 다음 조건을 실행합니다. Ctrl-C는 실행 중인 자식 프로세스 그룹을 종료하고 확보한 기록을 저장합니다. SIGKILL·전원 차단처럼 정리 코드를 실행할 수 없는 중단에서는 마지막 완료 실행의 체크포인트까지만 남을 수 있습니다.

## 3. 결과 확인·회수

시작할 때 출력하는 고유 결과 폴더는 새 clone의 `benchmarks/runs/zipformer-<UTC시각>-<임의문자>/` 아래에 생성됩니다. 기존 결과 폴더를 재사용하거나 덮어쓰지 않습니다.

| 파일 | 내용 |
| --- | --- |
| `manifest.json` | 인자·전체 실행 순서·원본 프로브/도구/자산 SHA-256, 실제 WAV 프레임 수, 실행 상태, 시작/종료 sysfs 온도·클럭·governor 원시 값 |
| `environment.json` | 지정한 Python의 실제 버전·실행 파일·prefix, 설치 패키지 버전 목록; pip 없는 uv 환경도 지원 |
| `environment-check.txt` | 환경 검사 표준 출력·오류. 사전 검사 실패 시 확인 |
| `runs.json` | 완료한 각 워밍업/측정의 상태·로그 위치·지연·자원 요약 |
| `summary.json`, `summary.md` | 스레드별 성공·실패·미실행, 지표별 유효 n, 평균·p50/p95·최대. 워밍업 제외 |
| `각 실행/probe.txt` | 해당 자식의 표준 출력·오류 원문. 최종 가설 포함 |
| `각 실행/resources.jsonl` | 기본 100ms 간격의 주 자식 PID CPU/RSS 표본. 오디오 청크 이벤트와 다른 자료 |
| `각 실행/result.json` | 해당 실행의 반환 코드·파싱 결과·실패 사유 |

RP5에서 마지막 출력에 나온 `summary.md`와 `summary.json`을 확인하고 전달하면 스레드별 변동성을 비교할 수 있습니다. 이 파일의 생성 자체가 실제 추론 성공을 뜻하지 않습니다. `manifest.status`, 성공/계획 수와 실패 로그를 함께 확인하세요. 반환 코드는 전체 성공 0, 추론·파싱·워밍업 실패/시간 초과 1, 환경·자산·실행기 오류 2, 사용자 중단 130입니다.

Host로 전체 실행 폴더를 회수할 때는 새 로컬 폴더를 사용합니다.

```bash
mkdir -p results/rp5-repeat-20261028-01
rsync -rz --ignore-existing \
  rp5:/home/deepet/projects/edge-asr-git/benchmarks/runs/ \
  results/rp5-repeat-20261028-01/
```

`rp5`는 Host의 SSH 별칭이며 이 결과 회수에는 별도 SSH 인증이 필요합니다. 현재 작업 환경의 키 인증은 거절되었으며 공개 GitHub clone/pull에는 영향을 주지 않습니다. 원시 결과는 Git 제외 경로에 보관합니다. 검토한 작은 요약만 `results/summaries/`에 복사해 버전 관리합니다. 모든 로그와 자동 요약은 원시 프로브의 전사·개인정보 포함 가능성을 확인하세요.

## 측정 범위와 해석

- **지연**은 기존 프로브의 Summary를 그대로 읽습니다. 첫 부분 출력은 입력 대기 포함·결과 조회 전의 근사 시각이고, 최종 지연은 파일 끝 기준이며 합성 무음 flush를 포함합니다. 실제 인식 지연·화면 표시 지연이 아닙니다. 최대 마감 초과는 큐 백로그가 아닙니다.
- **워밍업**도 매번 새로운 프로세스와 모델입니다. 파일/시스템 캐시 예열이며 상주 모델 내부 상태를 예열하지 않습니다. 시작 전 자산 해시 계산에서도 파일을 읽으므로 cold storage 벤치마크가 아닙니다. 원본 프로브의 지연 타이머는 모델 로딩·WAV 읽기 뒤에 시작합니다.
- **평균 CPU**는 자식의 CPU 시간 증가 / 부모가 관측한 실행 시작~종료 감지 wall time × 100입니다. 분모에는 폴링 종료 감지 지연(명목상 표본 간격 이내, 스케줄 지연 시 증가)과 관리 비용도 포함되어 근사값입니다. `resource.getrusage(RUSAGE_CHILDREN)` 차분을 사용하고 자식이 회수한 자손도 포함합니다. 코어 하나가 100%입니다. 모델 로딩·WAV 읽기·입력 대기·최종 출력까지 포함하며 steady-state 추론 CPU가 아닙니다. 부모 계측기 CPU는 제외합니다.
- **표본 CPU/RSS**는 주 자식 PID만 `/proc/PID/stat`에서 기본 100ms마다 읽습니다. CPU는 표본 간 user+system 시간 차분, RSS는 resident pages × 실제 페이지 크기입니다. 시작·종료 피크와 표본 사이 피크를 놓칠 수 있으며 RSS는 커널의 근사 계수입니다. 최고 RSS는 관측 표본 최대이지 정확한 high-water mark가 아닙니다. 수집 불가 시 `null`/누락 수를 남깁니다. [Linux 커널의 /proc 문서](https://www.kernel.org/doc/html/latest/filesystems/proc.html)를 기준으로 합니다.
- **분위수**는 성공한 측정 실행만 스레드별로 모아 nearest-rank로 산출합니다. 미계측 부분 가설은 제외하고 별도 개수를 기록하며 0으로 대체하지 않습니다. 유효 관측 2개 미만이면 p50/p95는 `null`입니다. 실패·중단·미실행은 성공 통계에서 제외하되 분모와 사유를 남깁니다. 적은 n의 p95는 불안정합니다.
- **한계**: 같은 WAV를 반복하므로 독립된 발화 30개가 아닙니다. CER·실제 인식 지연·RTF·드롭·백로그·지속적인 throttling은 미계측입니다. 전체 프로세스 시간 또는 replay 경과 시간으로 일괄 처리 RTF를 만들지 않습니다. ASR 서비스를 상주시킨 장시간 실시간 검증도 아닙니다.

다음 구현은 모델을 한 번 로드하는 공통 어댑터·실행기, 정답 manifest와 CER, 청크/발화 이벤트 계측입니다. 이번 도구의 프로브 기반 지표와 해당 실행기의 계측점을 구분해 비교해야 합니다.
