"""The native Direct3D 11 renderer, behind the same interface as ``DepthOverlay``.

``native/winduo2_native.cpp`` does the whole effect on the GPU: Desktop
Duplication's texture never leaves video memory, the blur pyramid is built by
draw calls, and the overlay is a DirectComposition swap chain that DWM composes
without reading anything back. Fades are DirectComposition animations. This
module loads the DLL with ctypes and wraps it as ``NativeOverlay``, which the
engine drives exactly like the Qt/OpenGL ``DepthOverlay``, and ``NativeCapture``,
which stands in for ``ScreenCapture``: the picture is pulled inside the DLL, so
``take()`` only says whether to pull one.

The DLL is optional. Without it, or when it cannot start on a machine, the
engine falls back to the OpenGL renderer.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import QTimer

from winduo2.log import get_logger

log = get_logger("native")

__all__ = ["NativeCapture", "NativeOverlay", "PULL", "available", "last_error", "selftest"]

DLL_NAME = "winduo2_native.dll"

#: The C API version this module speaks; see WD_VERSION in the C++.
API_VERSION = 1

PROFILE_SAMPLES = 128

_DREW = 1
_NEW_PICTURE = 2
_HAS_PICTURE = 4

#: DXGI_MODE_ROTATION_IDENTITY. Rotated outputs are left to the OpenGL path.
_UPRIGHT = (0, 1)


class _Params(ctypes.Structure):
    _fields_ = [
        ("profile", ctypes.c_float * (PROFILE_SAMPLES * 2)),
        ("profile_end", ctypes.c_float),
        ("profile_slope", ctypes.c_float),
        ("max_radius", ctypes.c_float),
        ("blur_strength", ctypes.c_float),
        ("blur_floor", ctypes.c_float),
        ("max_dim", ctypes.c_float),
        ("dim_strength", ctypes.c_float),
        ("dim_floor", ctypes.c_float),
        ("dim_reach", ctypes.c_float),
        ("hinge_glow", ctypes.c_float),
        ("reflection_intensity", ctypes.c_float),
        ("turn", ctypes.c_float),
        ("picture_fade", ctypes.c_float),
    ]


class _Info(ctypes.Structure):
    _fields_ = [
        ("screen_width", ctypes.c_int),
        ("screen_height", ctypes.c_int),
        ("picture_width", ctypes.c_int),
        ("picture_height", ctypes.c_int),
        ("texture_width", ctypes.c_int),
        ("texture_height", ctypes.c_int),
        ("inset", ctypes.c_int),
        ("max_level", ctypes.c_int),
        ("rotation", ctypes.c_int),
        ("excluded", ctypes.c_int),
    ]


class _Timings(ctypes.Structure):
    _fields_ = [
        ("capture_ms", ctypes.c_float),
        ("render_ms", ctypes.c_float),
        ("present_ms", ctypes.c_float),
    ]


_lib = None
_load_tried = False


def dll_path() -> Path:
    """Next to this module, in a source checkout and in the packaged app alike."""
    return Path(__file__).with_name(DLL_NAME)


def _load():
    global _lib, _load_tried
    if _load_tried:
        return _lib
    _load_tried = True
    if sys.platform != "win32":
        return None
    path = dll_path()
    if not path.exists():
        log.info("no native renderer at %s", path)
        return None
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as error:
        log.warning("could not load the native renderer: %s", error)
        return None
    lib.wd_version.restype = ctypes.c_int
    if lib.wd_version() != API_VERSION:
        log.warning(
            "the native renderer speaks version %d, not %d", lib.wd_version(), API_VERSION
        )
        return None
    lib.wd_last_error.restype = ctypes.c_char_p
    lib.wd_create.argtypes = [ctypes.c_int]
    lib.wd_create.restype = ctypes.c_void_p
    lib.wd_destroy.argtypes = [ctypes.c_void_p]
    lib.wd_destroy.restype = None
    lib.wd_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Info)]
    lib.wd_info.restype = None
    lib.wd_hwnd.argtypes = [ctypes.c_void_p]
    lib.wd_hwnd.restype = ctypes.c_void_p
    lib.wd_capture.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.wd_capture.restype = ctypes.c_int
    lib.wd_frame.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_Params), ctypes.c_int, ctypes.POINTER(_Timings)
    ]
    lib.wd_frame.restype = ctypes.c_int
    lib.wd_blank.argtypes = [ctypes.c_void_p]
    lib.wd_blank.restype = None
    lib.wd_clear.argtypes = [ctypes.c_void_p]
    lib.wd_clear.restype = ctypes.c_int
    lib.wd_opacity.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.wd_opacity.restype = ctypes.c_int
    lib.wd_selftest.restype = ctypes.c_int
    _lib = lib
    return lib


def available() -> bool:
    return _load() is not None


def last_error() -> str:
    lib = _load()
    if lib is None:
        return "the native renderer is not available"
    return (lib.wd_last_error() or b"").decode(errors="replace")


def selftest() -> tuple[bool, str]:
    """Shaders and pipeline on WARP, with no display or GPU needed."""
    lib = _load()
    if lib is None:
        return False, last_error()
    code = lib.wd_selftest()
    return code == 0, last_error()


#: What ``NativeCapture.take`` hands the engine: "pull a new picture". The
#: engine passes it on to ``NativeOverlay.submit`` as it would a frame.
PULL = object()


class NativeCapture:
    """Stands in for ``ScreenCapture``; Desktop Duplication lives in the DLL."""

    backend_name = "Desktop Duplication (native)"

    def __init__(self, overlay: NativeOverlay) -> None:
        self._overlay = overlay
        self._running = False

    @property
    def native_size(self) -> tuple[int, int]:
        info = self._overlay.info
        return (info.screen_width, info.screen_height) if info else (0, 0)

    @property
    def size(self) -> tuple[int, int]:
        return self._overlay.picture_size or (0, 0)

    @property
    def is_running(self) -> bool:
        return self._running and self._overlay.is_usable

    def start(self) -> None:
        # Quick: the first frame arrives on a later wd_frame.
        self._running = self._overlay.capture(True)
        if self._running:
            log.info(
                "capturing %dx%d via %s, rendering at %dx%d",
                *self.native_size, self.backend_name, *self.size,
            )

    def stop(self) -> None:
        if self._running:
            self._overlay.capture(False)
        self._running = False

    def take(self):
        return PULL if self.is_running else None


class NativeOverlay:
    """``DepthOverlay``'s interface over the native renderer."""

    FADE_IN = 0.07
    FADE_OUT = 0.22
    RAISE_INTERVAL = 0.25

    def __init__(self) -> None:
        self._lib = _load()
        self._handle = None
        self.info: _Info | None = None
        self._shown = False
        self._revealed = False
        self._has_picture = False
        self._placeholder = False
        self._stats = None
        self._raised_at = 0.0
        self._params = _Params()
        self._timings = _Timings()
        self.last_upload: float | None = None
        self._clear_after_fade = QTimer()
        self._clear_after_fade.setSingleShot(True)
        self._clear_after_fade.timeout.connect(self._clear_if_hidden)

    name = "Direct3D 11"

    # --- The interface the engine uses -----------------------------------

    @property
    def is_visible(self) -> bool:
        return self._shown

    @property
    def is_usable(self) -> bool:
        return self._handle is not None

    @property
    def has_picture(self) -> bool:
        return self._has_picture

    @property
    def showing_stand_in(self) -> bool:
        return self._has_picture and self._placeholder

    @property
    def needs_picture(self) -> bool:
        return not self._has_picture or self._placeholder

    @property
    def excludes_itself_from_capture(self) -> bool:
        return bool(self.info and self.info.excluded)

    @property
    def picture_size(self) -> tuple[int, int] | None:
        if self.info is None:
            return None
        return (self.info.picture_width, self.info.picture_height)

    def attach_stats(self, stats) -> None:
        self._stats = stats

    def warm_up(self, geometry_rect, screen_size, picture_size) -> bool:
        """Make the renderer and its window, transparent, before any lid moves."""
        if self._lib is None:
            return False
        width_cap = int(picture_size[0]) if picture_size[0] < screen_size[0] else 0
        if self._handle is not None:
            if self.picture_size == (int(picture_size[0]), int(picture_size[1])):
                return True
            self.destroy()
        handle = self._lib.wd_create(width_cap)
        if not handle:
            log.warning("the native renderer would not start: %s", last_error())
            return False
        info = _Info()
        self._lib.wd_info(handle, ctypes.byref(info))
        if info.rotation not in _UPRIGHT:
            log.info("the display is rotated; leaving it to the OpenGL renderer")
            self._lib.wd_destroy(handle)
            return False
        self._handle = handle
        self.info = info
        log.info(
            "native renderer ready: %dx%d, picture %dx%d, %d blur levels%s",
            info.screen_width, info.screen_height, info.picture_width,
            info.picture_height, info.max_level,
            "" if info.excluded else ", NOT excluded from capture",
        )
        return True

    def show(self, geometry_rect, screen_size, picture_size) -> bool:
        if not self.warm_up(geometry_rect, screen_size, picture_size):
            return False
        self._clear_after_fade.stop()
        self._lib.wd_opacity(self._handle, 0.0, 0.0, 0.0)
        self._shown = True
        self._revealed = False
        self._raise()
        return True

    def submit(self, frame, params) -> None:
        if not self._shown or self._handle is None:
            return
        now = time.monotonic()
        if now - self._raised_at >= self.RAISE_INTERVAL:
            self._raise()
        if not self._fill(params):
            return
        flags = self._lib.wd_frame(
            self._handle, ctypes.byref(self._params), 1 if frame is PULL else 0,
            ctypes.byref(self._timings),
        )
        timings = self._timings
        self.last_upload = timings.capture_ms / 1000.0 if flags & _NEW_PICTURE else None
        if flags & _NEW_PICTURE:
            self._placeholder = False
        self._has_picture = bool(flags & _HAS_PICTURE)
        if self._stats is not None and flags & _DREW:
            self._stats.painted(timings.render_ms / 1000.0)
            self._stats.present_took(timings.present_ms / 1000.0)
        if self._has_picture and not self._revealed:
            self._revealed = True
            self._lib.wd_opacity(self._handle, 0.0, 1.0, self.FADE_IN)

    def blank_picture(self) -> None:
        if self._handle is None:
            return
        self._lib.wd_blank(self._handle)
        self._has_picture = True
        self._placeholder = True
        # Presented now, not on the next frame: until something is presented
        # the screen keeps the last frame, which is the very picture from
        # before the lid shut that must not be seen when it opens.
        if self._params.profile_end > 0.0:
            self._params.picture_fade = 0.0
            self._lib.wd_frame(
                self._handle, ctypes.byref(self._params), 0, ctypes.byref(self._timings)
            )

    def dismiss(self, animated: bool = True) -> None:
        if not self._shown or self._handle is None:
            return
        self._shown = False
        was_revealed, self._revealed = self._revealed, False
        if animated and was_revealed:
            self._lib.wd_opacity(self._handle, 1.0, 0.0, self.FADE_OUT)
            # DirectComposition runs the fade; the picture is dropped after it.
            self._clear_after_fade.start(int(self.FADE_OUT * 1000) + 30)
        else:
            self._lib.wd_opacity(self._handle, 0.0, 0.0, 0.0)
            self._clear_if_hidden()

    def destroy(self) -> None:
        self._clear_after_fade.stop()
        handle, self._handle = self._handle, None
        self.info = None
        self._shown = False
        self._has_picture = False
        if handle:
            self._lib.wd_destroy(handle)

    # --- For NativeCapture ------------------------------------------------

    def make_capture(self) -> NativeCapture:
        return NativeCapture(self)

    def capture(self, on: bool) -> bool:
        if self._handle is None:
            return False
        ok = bool(self._lib.wd_capture(self._handle, 1 if on else 0))
        if on and not ok:
            log.warning("could not start Desktop Duplication: %s", last_error())
        return ok

    # --- Internals ----------------------------------------------------------

    def _clear_if_hidden(self) -> None:
        if not self._shown and self._handle is not None:
            self._lib.wd_clear(self._handle)
            self._has_picture = False
            self._placeholder = False

    def _raise(self) -> None:
        self._raised_at = time.monotonic()
        hwnd = self._lib.wd_hwnd(self._handle) if self._handle else None
        if not hwnd:
            return
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE NOMOVE NOACTIVATE SHOWWINDOW
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(hwnd), wintypes.HWND(-1), 0, 0, 0, 0, flags
        )

    def _fill(self, params) -> bool:
        """``FrameParams`` into the C structure. False if the frame cannot be drawn."""
        from winduo2.render.overlay import _profile_uniform

        table = _profile_uniform(params.profile)
        if table is None or table.size != PROFILE_SAMPLES * 2 or self.info is None:
            return False
        p = self._params
        ctypes.memmove(p.profile, table.ctypes.data, table.nbytes)
        p.profile_end = params.profile.end
        p.profile_slope = params.profile.slope
        scale = self.info.picture_width / max(self.info.screen_width, 1)
        p.max_radius = params.max_blur_radius * scale
        p.blur_strength = params.blur_strength
        p.blur_floor = params.blur_floor
        p.max_dim = params.max_dim
        p.dim_strength = params.dim_strength
        p.dim_floor = params.dim_floor
        p.dim_reach = params.dim_reach
        p.hinge_glow = params.hinge_glow
        p.reflection_intensity = params.reflection_intensity
        p.turn = params.turn_strength
        p.picture_fade = params.picture_fade
        return True
