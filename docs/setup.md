# 개발 및 RP5 환경 설치

## 역할과 Python 기준

Host는 소스 편집, 패키징, 모델 없이 실행하는 검사를 담당합니다. 성능 결론은 RP5에서 측정한 결과만으로 작성합니다. Host에서 성공한 설치·테스트는 ARM64 추론 검증이 아닙니다.

대상은 RP5 ARM64 / Debian 13 / RAM 8GB / Python 3.11입니다. [Debian 13 공식 릴리스 노트](https://www.debian.org/releases/trixie/release-notes/whats-new.en.html)에 따르면 기본 Python은 3.13입니다. `python3`를 3.11로 가정하거나 시스템 Python을 교체하지 않습니다. RP5에 별도 설치된 3.11의 경로를 확인해서 사용합니다. 없다면 별도 3.11 인터프리터를 준비한 뒤 진행해야 하며 기존 RP5 가상환경은 변경하지 않습니다.

## Host 기본 설치

```bash
cd /d/shin/edge-asr
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip check
python -m unittest discover -s tests -v
edge-asr env
edge-asr check-config configs/zipformer_streaming.toml
```

필수 런타임은 표준 라이브러리뿐입니다. Python 3.11의 `tomllib`을 사용합니다. Python 패키지와 선택 의존성은 `pyproject.toml`에서 관리합니다. `.venv/`와 빌드 결과는 Git에 포함하지 않습니다.

## RP5의 새 환경

기존 RP5 `.venv`와 모델을 사용하는 반복 도구는 추가 설치 없이 실행할 수 있습니다. [한 작업 폴더 통합·GitHub 업데이트 안내](deployment.md)를 먼저 사용하세요. 기존 `.venv`는 유지합니다. 아래는 새 비교 환경이 필요할 때만 사용하는 선택 절차이며 실제 RP5 실행을 확인한 기록이 아닙니다. `.venv-candidate`가 이미 있으면 그 환경을 덮어쓰지 말고 새 이름을 선택합니다.

```bash
uname -m                         # 대상 기대값: aarch64
cat /etc/os-release
command -v python3.11
python3.11 --version             # 3.11.x 확인
cd /home/deepet/projects/edge-asr
python3.11 -m venv .venv-candidate
source .venv-candidate/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
edge-asr env
```

`venv`/`ensurepip`가 없는 경우 해당 3.11 설치 방식에 맞는 구성요소를 먼저 준비합니다. Debian의 `python3-venv`는 기본 Python용이므로 별도 3.11을 자동으로 지원한다고 가정하지 않습니다.

## Zipformer 후보 의존성

```bash
# 새 RP5 가상환경 안에서만 실행
python -m pip install --only-binary=:all: 'numpy==1.26.4' 'sherpa-onnx==1.12.40'
python -m pip install -e '.[zipformer]'
python -m pip check
python -c 'import numpy, sherpa_onnx; print(numpy.__version__, sherpa_onnx.__version__)'
```

이 버전들은 새 실험을 위한 후보 고정값입니다. 기존 RP5 결과를 만든 패키지 버전은 기록되어 있지 않으며 동일 환경이라고 주장하지 않습니다. PyPI 배포 메타데이터에서 [NumPy 1.26.4](https://pypi.org/project/numpy/1.26.4/#files), [sherpa-onnx 1.12.40](https://pypi.org/project/sherpa-onnx/1.12.40/#files)의 CPython 3.11 Linux aarch64 wheel 존재를 확인한 것과 실제 RP5 설치·모델 실행 성공은 다릅니다. `sherpa-onnx`의 전이 의존성도 대상 환경에서 설치되어야 합니다. wheel을 찾지 못하면 자동 소스 빌드로 우회하지 말고 Python ABI·아키텍처·전이 의존성의 wheel 가용성을 점검합니다.

SenseVoice/Qwen3-ASR 런타임은 아직 의존성에 넣지 않았습니다. 모델 포맷과 실행 백엔드의 ARM64 지원을 검토한 뒤 별도 선택 의존성으로 추가합니다. 기본 설치에 PyTorch 등 무거운 패키지를 강제하지 않습니다.

## 자산 및 환경 기록

모델·WAV는 Git으로 전달하지 않습니다. 기존 RP5 자산은 수정하지 않고 새 배포 경로의 `models/`에서 심볼릭 링크로 참조할 수 있습니다. 예시 경로와 파일명은 [실험 README](../experiments/m1_zipformer/README.md)에 있습니다. 자산을 별도로 수급할 때는 배포처·라이선스·버전·SHA-256을 로컬 실행 기록에 남깁니다.

```bash
mkdir -p results/local-environment
edge-asr env > results/local-environment/environment.json
python -m pip freeze --all > results/local-environment/pip-freeze.txt
```

이는 **명령을 실행한 컴퓨터**의 정보입니다. 각 RP5 실행 결과에는 해당 장비에서 생성한 환경 정보, Git 커밋, 설정 사본, 모델·WAV 체크섬을 함께 보관합니다. `pip freeze`는 그 환경의 설치 스냅샷이며 Host의 가상환경이나 wheel을 ARM64로 복사하는 방법은 아닙니다. 전체 전이 의존성 lock과 해시 검증 설치는 RP5 후보 환경이 통과한 후 추가합니다.
