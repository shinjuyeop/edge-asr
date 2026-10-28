#!/usr/bin/env bash
# Host → RP5 소스 복사. 기본 dry-run, 원격 기존 파일은 덮어쓰거나 삭제하지 않음.
set -euo pipefail

usage() {
    echo "사용법: $0 USER@HOST /absolute/new/release/path [--apply]" >&2
}

if (( $# < 2 || $# > 3 )); then usage; exit 2; fi
target=$1
remote_dir=$2
if [[ ! $target =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.@-]*$ ]]; then
    echo "SSH 대상 형식을 확인하세요." >&2
    exit 2
fi
if [[ ! $remote_dir =~ ^/[a-zA-Z0-9_][a-zA-Z0-9_./-]*$ || /$remote_dir/ == *'/../'* || /$remote_dir/ == *'/./'* ]]; then
    echo "공백과 . 또는 .. 경로 요소가 없는 절대 배포 경로가 필요합니다." >&2
    exit 2
fi
options=(--dry-run)
if (( $# == 3 )); then
    if [[ $3 != --apply ]]; then usage; exit 2; fi
    options=()
fi
command -v rsync >/dev/null || { echo "Host에 rsync가 필요합니다." >&2; exit 1; }
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_root"
# --ignore-existing는 내용이 다른 기존 파일도 그대로 둠. 변경 배포는 새 경로를 사용.
# rsync는 -t/-p/-o/-g 옵션 없이 파일 내용만 복사한다.
rsync -rz --itemize-changes --ignore-existing --protect-args \
    --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.egg-info/' \
    --exclude='.git/' --exclude='.venv*/' --exclude='venv*/' --exclude='env/' \
    --exclude='.cache/' --exclude='cache/' --exclude='models/' --exclude='data/' \
    --exclude='*.onnx*' --exclude='*.pt' --exclude='*.pth' --exclude='*.bin' \
    --exclude='*.safetensors' --exclude='*.wav' --exclude='*.WAV' \
    --exclude='runs/' --exclude='artifacts/' \
    "${options[@]}" \
    README.md pyproject.toml .gitignore .gitattributes src configs benchmarks scripts tests docs experiments \
    "$target:${remote_dir%/}/"
