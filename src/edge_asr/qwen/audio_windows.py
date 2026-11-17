"""부분 요청에만 독립 인코더 창을 적용하는 선택 실험의 수명과 ABI 검사."""

import ctypes


def validate_audio_windows(model):
    window = model.get("partial_audio_window_s", 0)
    if "partial_audio_window_s" in model and (
        type(window) is not int or window not in (1, 2, 4, 8)
    ):
        raise ValueError("partial_audio_window_s는 1/2/4/8초 정수여야 합니다.")
    if "partial_audio_window_ms" in model:
        window = model["partial_audio_window_ms"]
        if (
            "partial_audio_window_s" in model
            or type(window) is not int
            or window not in (990, 1990)
        ):
            raise ValueError("partial_audio_window_ms는 초 설정 없이 990/1990ms만 지원합니다.")
    reuse = model.get("partial_audio_window_cache", True)
    if type(reuse) is not bool or ("partial_audio_window_cache" in model and not window):
        raise ValueError("partial_audio_window_cache에는 창 크기와 true/false가 필요합니다.")
    if window and not model.get("cache_text_prefix"):
        raise ValueError("부분 음성 창 실험에는 cache_text_prefix=true가 필요합니다.")
    if window and model.get("audio_feature_cache_manifest"):
        raise ValueError("독립 음성 창과 전체 log-Mel 캐시는 함께 사용할 수 없습니다.")
    return window, reuse


class PartialAudioWindows:
    def __init__(self, model, build):
        self.window, self.reuse = validate_audio_windows(model)
        self.milliseconds = "partial_audio_window_ms" in model
        feature = "partial_audio_window_ms_v1" if self.milliseconds else "partial_audio_window_v1"
        if feature not in build.get("features", []):
            raise ValueError(f"부분 음성 창에는 {feature} 별도 빌드가 필요합니다.")
        self.setter = self.handle = None
        self.pending = self.active = False

    def bind(self, library, handle):
        self.setter = getattr(
            library,
            "edge_gguf_set_audio_window_ms" if self.milliseconds else "edge_gguf_set_audio_window",
        )
        self.setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.setter.restype = ctypes.c_int
        self.handle = handle
        self.reset()

    def prepare(self, kind):
        if kind not in ("partial", "final"):
            raise ValueError("부분/확정 요청 구분이 필요합니다.")
        self.pending = kind == "partial"

    def begin(self):
        self.active, self.pending = self.pending, False
        self._set(self.window if self.active else 0, False)

    def reset(self):
        self.pending = self.active = False
        if self.handle is not None:
            self._set(0, True)

    def _set(self, window, reset):
        if self.setter is None or self.setter(self.handle, window, int(self.reuse), int(reset)):
            raise RuntimeError("부분 음성 창 설정/초기화 실패")

    def verify(self, result):
        row = result.get("audio_window", {})
        key = "window_ms" if self.milliseconds else "window_s"
        if row.get("active") is not self.active or row.get(key) != (
            self.window if self.active else 0
        ):
            raise RuntimeError("부분/확정 음성 창 적용 범위가 다릅니다.")

    def close(self):
        self.handle = self.setter = None
        self.pending = self.active = False
