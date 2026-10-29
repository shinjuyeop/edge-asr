"""기존 RP5 경로·가상환경을 유지하며 Git clone과 과거 배포본을 한 폴더로 통합."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPOSITORIES = {
    "https://github.com/shinjuyeop/edge-asr.git",
    "https://github.com/shinjuyeop/edge-asr",
    "git@github.com:shinjuyeop/edge-asr.git",
    "ssh://git@github.com/shinjuyeop/edge-asr.git",
}


def git(root: Path, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or f"Git 명령 실패: {args}")
    return result


def ensure_plain_path(root: Path, path: Path) -> None:
    """복사 대상 경로의 기존 symlink·파일/디렉터리 충돌을 거부한다."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"자동 통합할 수 없는 심볼릭 링크 경로: {current}")
        if current != path and current.exists() and not current.is_dir():
            raise ValueError(f"부모 경로가 디렉터리가 아닙니다: {current}")
    if path.exists() and not path.is_file():
        raise ValueError(f"파일 복사 대상과 디렉터리가 충돌합니다: {path}")


def active_benchmarks(roots: list[Path]) -> list[int]:
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().decode(errors="replace").split("\0")
            if not any(
                Path(arg).name in {"benchmark_zipformer.py", "streaming_probe.py"} for arg in args
            ):
                continue
            cwd = (cmdline.parent / "cwd").resolve()
            if any(
                cwd == root or root in cwd.parents or any(str(root) + "/" in arg for arg in args)
                for root in roots
            ):
                found.append(int(cmdline.parent.name))
        except (OSError, ValueError):
            continue
    return found


def plan(source: Path, target: Path, legacy: list[Path]) -> dict:
    if not Path("/proc/self/cmdline").is_file():
        raise ValueError("실행 중인 실험 확인을 위해 Linux에서 실행해야 합니다.")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("소스 clone과 통합 대상은 서로 다른 형제 경로여야 합니다.")
    if not source.is_dir() or not target.is_dir() or source.parent != target.parent:
        raise ValueError("기존 clone과 대상 프로젝트가 같은 부모 아래에 있어야 합니다.")
    if (source / ".venv").exists():
        raise ValueError("clone 안에 별도 .venv가 있습니다. 환경 경로를 먼저 확인해야 합니다.")
    if not (target / ".venv/bin/python").is_file() or not (target / "models").is_dir():
        raise ValueError("대상 폴더의 기존 .venv/bin/python 및 models를 확인하세요.")
    if not (source / ".git").is_dir() or (source / ".git").is_symlink():
        raise ValueError(
            "소스는 일반 Git clone이어야 합니다. worktree나 외부 .git 링크는 지원하지 않습니다."
        )
    if git(source, "rev-parse", "--show-toplevel").stdout.strip() != str(source):
        raise ValueError("소스가 Git 저장소 루트가 아닙니다.")
    if git(source, "remote", "get-url", "origin").stdout.strip() not in REPOSITORIES:
        raise ValueError("소스 origin이 shinjuyeop/edge-asr가 아닙니다.")
    if git(source, "symbolic-ref", "--short", "HEAD").stdout.strip() != "main":
        raise ValueError("소스는 main 브랜치에서 먼저 업데이트하세요.")
    if git(source, "status", "--porcelain", "--untracked-files=no").stdout:
        raise ValueError("소스 clone에 미커밋 수정이 있습니다. 통합 전에 보존·확인하세요.")
    revision = git(source, "rev-parse", "HEAD").stdout.strip()
    if revision != git(source, "rev-parse", "origin/main").stdout.strip():
        raise ValueError("소스 HEAD와 origin/main이 다릅니다. 소스 커밋을 먼저 확인하세요.")
    if git(source, "config", "--get", "core.worktree", check=False).returncode == 0:
        raise ValueError("외부 core.worktree 설정은 자동 복사할 수 없습니다.")
    if any((source / ".git").rglob("*.lock")) or (source / ".git/objects/info/alternates").exists():
        raise ValueError(
            "Git lock 또는 외부 object 의존성이 있습니다. Git 작업 완료·구조 확인이 필요합니다."
        )
    if git(source, "check-ignore", "-q", ".local-backups/check", check=False).returncode != 0:
        raise ValueError(
            "소스에 백업 Git 제외 규칙이 없습니다. 먼저 git pull --ff-only origin main을 실행하세요."
        )
    old_git = target / ".git"
    if old_git.exists() or old_git.is_symlink():
        if not old_git.is_dir() or old_git.is_symlink():
            raise ValueError(
                "대상의 .git이 일반 디렉터리가 아닙니다. 기존 Git 구조를 먼저 확인하세요."
            )
        if (
            any(old_git.rglob("*.lock"))
            or git(target, "config", "--get", "core.worktree", check=False).returncode == 0
        ):
            raise ValueError("대상 Git에 lock 또는 외부 작업 폴더 설정이 있습니다.")
        old_head = git(target, "rev-parse", "--verify", "HEAD", check=False)
        if old_head.returncode == 0:
            origin = git(target, "remote", "get-url", "origin", check=False).stdout.strip()
            if (
                origin not in REPOSITORIES
                or git(
                    source,
                    "merge-base",
                    "--is-ancestor",
                    old_head.stdout.strip(),
                    revision,
                    check=False,
                ).returncode
                != 0
            ):
                raise ValueError(
                    "대상에 다른 저장소 또는 소스에 없는 커밋이 있습니다. 기존 이력을 먼저 확인하세요."
                )
    backup_root = target / ".local-backups"
    if backup_root.is_symlink() or (backup_root.exists() and not backup_root.is_dir()):
        raise ValueError("백업 경로는 대상 내부의 일반 디렉터리여야 합니다.")
    if source.stat().st_dev != target.stat().st_dev:
        raise ValueError("소스와 대상은 같은 파일시스템에 있어야 합니다.")
    archives = [source]
    for directory in legacy:
        if not directory.exists():
            continue
        if directory.parent != target.parent or directory == target or directory == source:
            raise ValueError("과거 배포본은 다른 형제 디렉터리여야 합니다.")
        if (
            not directory.is_dir()
            or directory.stat().st_dev != target.stat().st_dev
            or (directory / ".venv").exists()
        ):
            raise ValueError("과거 배포본의 파일시스템·가상환경 구조를 먼저 확인하세요.")
        if directory not in archives:
            archives.append(directory)
    pids = active_benchmarks([target, *archives])
    if pids:
        raise ValueError(f"벤치마크가 실행 중입니다. 종료 후 통합하세요. PID: {pids}")
    files, changed = [], []
    for name in git(source, "ls-files", "-z").stdout.split("\0"):
        if not name:
            continue
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or ".git" in relative.parts
            or relative.parts[0] == ".local-backups"
        ):
            raise ValueError(f"허용하지 않는 추적 경로: {name}")
        if relative.parts[0].startswith(".venv") or relative.parts[0] in {"venv", "env"}:
            raise ValueError(f"가상환경 파일은 통합 소스에 포함할 수 없습니다: {name}")
        if relative.suffix.lower() in {
            ".onnx",
            ".wav",
            ".flac",
            ".pcm",
            ".pt",
            ".pth",
            ".bin",
            ".safetensors",
            ".gguf",
        }:
            raise ValueError(f"모델·오디오 바이너리는 통합 소스에 포함할 수 없습니다: {name}")
        if relative.parts[0] in {"models", "data"} and name not in {
            "models/README.md",
            "data/README.md",
        }:
            raise ValueError(f"모델·데이터 자산은 통합 소스에 포함할 수 없습니다: {name}")
        origin, destination = source / relative, target / relative
        ensure_plain_path(source, origin)
        ensure_plain_path(target, destination)
        if not origin.is_file():
            raise ValueError(f"일반 파일이 아닌 추적 경로: {name}")
        if (
            destination.exists()
            and destination.read_bytes() == origin.read_bytes()
            and (destination.stat().st_mode & 0o777) == (origin.stat().st_mode & 0o777)
        ):
            continue
        files.append(name)
        if destination.exists():
            changed.append(name)
    return {
        "source": str(source),
        "target": str(target),
        "commit": revision,
        "copy_files": files,
        "backup_files": changed,
        "archive_directories": [str(path) for path in archives],
        "preserved_environment": str(target / ".venv"),
        "preserved_models": str(target / "models"),
    }


def apply_plan(prepared: dict) -> Path:
    source, target = Path(prepared["source"]), Path(prepared["target"])
    backup_root = target / ".local-backups"
    backup_root.mkdir(exist_ok=True)
    prefix = datetime.now(timezone.utc).strftime("consolidate-%Y%m%dT%H%M%SZ-")
    backup = Path(tempfile.mkdtemp(prefix=prefix, dir=backup_root))
    receipt = {**prepared, "status": "preparing", "archive_mapping": {}}
    receipt_path = backup / "receipt.json"

    def copy_file(origin, destination):
        # 기존 파일이 hard link여도 다른 링크의 바이트를 변경하지 않는다.
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".consolidate-", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copy2(origin, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def save():
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    save()
    written, moved = [], []
    old_git_moved = False
    new_git_installed = False
    try:
        for name in prepared["backup_files"]:
            destination = backup / "original-files" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target / name, destination)
        shutil.copytree(source / ".git", backup / "incoming.git", symlinks=True)
        receipt["status"] = "applying"
        save()
        for name in prepared["copy_files"]:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            written.append(name)
            copy_file(source / name, destination)
        if (target / ".git").exists():
            old_git_moved = True
            (target / ".git").rename(backup / "original.git")
        new_git_installed = True
        (backup / "incoming.git").rename(target / ".git")
        if git(target, "status", "--porcelain", "--untracked-files=no").stdout:
            raise ValueError("통합 후 추적 소스가 HEAD와 일치하지 않습니다.")
        if (
            git(
                target, "check-ignore", "-q", str(backup.relative_to(target)), check=False
            ).returncode
            != 0
        ):
            raise ValueError("백업 폴더가 Git에서 제외되지 않았습니다.")
        for old in map(Path, prepared["archive_directories"]):
            destination = backup / old.name
            moved.append((old, destination))
            old.rename(destination)
            receipt["archive_mapping"][str(old)] = str(destination)
            save()
        receipt["status"] = "completed"
        save()
    except BaseException as error:
        # 기존 파일을 복원하고 이번 작업에서 새로 만든 파일만 제거한다.
        receipt["status"], receipt["error"] = "rolling_back", str(error)
        save()
        for old, archived in reversed(moved):
            if archived.exists() and not old.exists():
                archived.rename(old)
        if new_git_installed and (target / ".git").exists():
            (target / ".git").rename(backup / "rolled-back-incoming.git")
        if old_git_moved and (backup / "original.git").exists():
            (backup / "original.git").rename(target / ".git")
        for name in reversed(written):
            destination = target / name
            if name in prepared["backup_files"]:
                copy_file(backup / "original-files" / name, destination)
            else:
                destination.unlink(missing_ok=True)
        receipt["rolled_back_archive_mapping"] = dict(receipt["archive_mapping"])
        receipt["archive_mapping"] = {}
        receipt["status"], receipt["error"] = "rolled_back", str(error)
        save()
        print(f"통합을 되돌렸습니다. 보존·복원 기록: {receipt_path}")
        raise
    return receipt_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="RP5 작업 폴더를 기존 edge-asr 하나로 통합 (기본 미리보기)"
    )
    projects = Path.home() / "projects"
    parser.add_argument("--source", type=Path, default=projects / "edge-asr-git")
    parser.add_argument("--target", type=Path, default=projects / "edge-asr")
    parser.add_argument(
        "--legacy-dir",
        type=Path,
        action="append",
        help="함께 보관할 형제 배포 폴더. 생략 시 edge-asr-bench-20261028-01",
    )
    parser.add_argument(
        "--apply", action="store_true", help="검증 후 실제 통합. 기존 자료는 내부 백업에 보존"
    )
    args = parser.parse_args(argv)
    try:
        source, target = args.source.expanduser().resolve(), args.target.expanduser().resolve()
        legacy = (
            args.legacy_dir
            if args.legacy_dir is not None
            else [target.parent / "edge-asr-bench-20261028-01"]
        )
        prepared = plan(source, target, [p.expanduser().resolve() for p in legacy])
        print(json.dumps(prepared, ensure_ascii=False, indent=2))
        if args.apply:
            # 미리보기 이후의 상태 변화를 줄이기 위해 적용 직전에 한 번 더 검사.
            prepared = plan(source, target, [p.expanduser().resolve() for p in legacy])
            receipt = apply_plan(prepared)
            print(f"통합 완료. 앞으로 사용할 폴더: {target}")
            print(f"보존·이동 기록: {receipt}")
            print(f"다음 명령: cd {target}")
        else:
            print("미리보기 완료. 실제 복사·이동 없음. 적용하려면 --apply를 추가하세요.")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"통합 중단: {error}")
        return 2
    except KeyboardInterrupt:
        print("사용자가 통합을 중단했습니다. 적용이 시작됐다면 복원 기록을 확인하세요.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
