"""설치 패키지와 sherpa 네이티브 ORT를 구분한다. 기본 조회는 런타임을 import하지 않는다."""

import platform
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distributions, version

from edge_asr import __version__


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def runtime_info(*, probe=False):
    info = {
        "sherpa_package_version": package_version("sherpa-onnx"),
        "sherpa_core_package_version": package_version("sherpa-onnx-core"),
        "python_onnxruntime_package_version": package_version("onnxruntime"),
        "sherpa_native_onnxruntime_version": None,
        "native_version_source": "sherpa_onnx.onnxruntime_version",
        "status": "not_imported",
        "reason": "선택 런타임을 아직 import하지 않았습니다.",
    }
    module = sys.modules.get("sherpa_onnx")
    if module is None and probe:
        try:
            module = import_module("sherpa_onnx")
        except Exception as error:
            info.update(status="unavailable", reason=f"{type(error).__name__}: {error}")
            return info
    if module is not None:
        try:
            native = getattr(module, "onnxruntime_version", None)
            native = native() if callable(native) else native
            if not isinstance(native, str) or not native.strip():
                info.update(
                    status="native_version_unavailable",
                    reason="네이티브 ORT 버전 문자열을 제공하지 않는 런타임입니다.",
                )
            else:
                info.update(
                    sherpa_native_onnxruntime_version=native, status="available", reason=None
                )
        except Exception as error:
            info.update(
                status="native_version_unavailable", reason=f"{type(error).__name__}: {error}"
            )
    return info


def environment() -> dict:
    return {
        "edge_asr": __version__,
        "python": sys.version,
        "executable": sys.executable,
        "machine": platform.machine(),
        "platform": platform.platform(),
        "runtime": runtime_info(),
        "packages": sorted(
            [{"name": d.metadata["Name"], "version": d.version} for d in distributions()],
            key=lambda row: row["name"] or "",
        ),
    }
