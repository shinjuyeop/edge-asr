"""선택 log-Mel 접두 캐시의 자산·preload·상태 수명 검증."""

import ctypes
import json
import os

from edge_asr.artifacts import digest
from edge_asr.dataset import resolve_path


class AudioFeatureCache:
    def __init__(self, manifest, mode, root, commit):
        if mode not in ("off", "verify", "on"):
            raise ValueError("음성 특징 캐시는 off/verify/on 중 하나입니다.")
        path = resolve_path(manifest, root)
        self.build = json.loads(path.read_text())
        if (
            self.build.get("feature") != "raw_log_mel_prefix_v1"
            or self.build.get("upstream_commit") != commit
        ):
            raise ValueError("음성 특징 캐시 기능/엔진 커밋이 다릅니다.")
        self.path = resolve_path(self.build["library"], root)
        engine = resolve_path(self.build["mtmd_library"], root)
        if (
            digest(self.path) != self.build["library_sha256"]
            or digest(engine) != self.build["mtmd_sha256"]
        ):
            raise ValueError("음성 특징 캐시/mtmd 해시가 다릅니다.")
        preload = os.environ.get("LD_PRELOAD", "").replace(":", " ").split()
        if [resolve_path(p, root) for p in preload] != [self.path] or resolve_path(
            os.environ.get("EDGE_ASR_MEL_MTMD", ""), root
        ) != engine:
            raise ValueError(
                "음성 특징 캐시에는 해당 LD_PRELOAD/EDGE_ASR_MEL_MTMD만 지정해야 합니다."
            )
        self.mode = {"off": 0, "verify": 2, "on": 1}[mode]
        self.files = {
            "feature_cache_manifest": path,
            "feature_cache_library": self.path,
            "feature_cache_mtmd": engine,
        }
        self.library = None

    def bind(self):
        library = ctypes.CDLL(str(self.path), mode=os.RTLD_NOLOAD)
        process = ctypes.CDLL(None)
        symbol = "_Z18mtmd_audio_log_melRK21mtmd_audio_mel_configPKfS3_lPf"
        for name in (symbol, "edge_mel_begin", "edge_mel_result"):
            if (
                ctypes.cast(getattr(library, name), ctypes.c_void_p).value
                != ctypes.cast(getattr(process, name), ctypes.c_void_p).value
            ):
                raise RuntimeError("음성 특징 캐시 preload 심볼이 적용되지 않았습니다.")
        library.edge_mel_begin.argtypes = [ctypes.c_int, ctypes.c_int]
        library.edge_mel_begin.restype = ctypes.c_int
        library.edge_mel_result.argtypes = []
        library.edge_mel_result.restype = ctypes.c_char_p
        library.edge_mel_release.argtypes = []
        library.edge_mel_release.restype = ctypes.c_int
        self.library = library
        self.reset()

    def begin(self, *, reset=False):
        if self.library is None or self.library.edge_mel_begin(self.mode, int(reset)) != 0:
            raise RuntimeError("음성 특징 캐시 초기화 실패")

    def reset(self):
        if self.library is not None:
            self.begin(reset=True)

    def result(self):
        value = self.library.edge_mel_result()
        if not value:
            raise RuntimeError("음성 특징 캐시가 아직 실행 중입니다.")
        row = json.loads(value)
        if row["mode"] != self.mode or row["calls"] < 0 or row["cache_capacity_bytes"] > 1024**2:
            raise RuntimeError("음성 특징 캐시 모드·적용·메모리 상한 검증 실패")
        return row

    def close(self):
        if self.library is not None:
            if self.library.edge_mel_release() != 0:
                raise RuntimeError("실행 중인 음성 특징 캐시는 닫을 수 없습니다.")
            self.library = None
