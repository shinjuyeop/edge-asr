"""검증한 FP16 합성곱 후보의 명시적 선택·자산 검사·호출 기록."""

import ctypes
import json
import os

from edge_asr.artifacts import digest
from edge_asr.dataset import resolve_path


class ConvolutionTile:
    def __init__(self, manifest, root, llama_commit):
        path = resolve_path(manifest, root)
        self.build = json.loads(path.read_text())
        if (
            self.build.get("feature") != "conv_tile4_1to5_v1"
            or self.build.get("upstream_commit") != llama_commit
        ):
            raise ValueError("합성곱 후보의 기능·upstream 커밋이 다릅니다.")
        self.path = resolve_path(self.build["library"], root)
        cpu = resolve_path(self.build["cpu_library"], root)
        if (
            digest(self.path) != self.build["library_sha256"]
            or digest(cpu) != self.build["original_cpu_sha256"]
        ):
            raise ValueError("합성곱 후보·원래 CPU 라이브러리 해시가 다릅니다.")
        preload = os.environ.get("LD_PRELOAD", "").replace(":", " ").split()
        if [resolve_path(p, root) for p in preload] != [self.path] or resolve_path(
            os.environ.get("EDGE_ASR_CONV_CPU", ""), root
        ) != cpu:
            raise ValueError("합성곱 후보에는 해당 LD_PRELOAD·EDGE_ASR_CONV_CPU만 지정해야 합니다.")
        self.files = {
            "conv_library": self.path,
            "conv_manifest": path,
            "conv_original_cpu": cpu,
        }
        self.library = None

    def bind(self):
        # 실행 중 뒤늦게 로드해 interposition이 적용된 것처럼 기록하지 않는다.
        library = ctypes.CDLL(str(self.path), mode=os.RTLD_NOLOAD)
        process = ctypes.CDLL(None)
        for name in ("llamafile_sgemm", "edge_conv_begin", "edge_conv_result"):
            if (
                ctypes.cast(getattr(library, name), ctypes.c_void_p).value
                != ctypes.cast(getattr(process, name), ctypes.c_void_p).value
            ):
                raise RuntimeError("합성곱 후보가 전역 심볼로 연결되지 않았습니다.")
        library.edge_conv_begin.argtypes = [ctypes.c_int]
        library.edge_conv_begin.restype = ctypes.c_int
        library.edge_conv_result.argtypes = []
        library.edge_conv_result.restype = ctypes.c_char_p
        self.library = library

    def begin(self):
        if self.library is None or self.library.edge_conv_begin(2) != 0:
            raise RuntimeError("합성곱 후보 초기화 실패")

    def result(self):
        blob = self.library.edge_conv_result()
        if not blob:
            raise RuntimeError("합성곱 후보 호출이 아직 실행 중입니다.")
        result = json.loads(blob)
        if result.get("mode") != 2 or any(g["calls"] != g["accepted"] for g in result["groups"]):
            raise RuntimeError("합성곱 후보 모드·커널 수락 검증 실패")
        return result
