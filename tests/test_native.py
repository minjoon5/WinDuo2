"""The ctypes side of the native renderer, and the DLL itself where it is built."""

from __future__ import annotations

import ctypes
import sys

import pytest

from winduo2.render import native


def test_structures_match_the_cpp_layouts():
    # The same sizes the C++ static_asserts; a mismatch would pass garbage.
    assert ctypes.sizeof(native._Params) == 1076
    assert ctypes.sizeof(native._Info) == 40
    assert ctypes.sizeof(native._Timings) == 12


def test_the_profile_holds_every_sample_the_geometry_makes():
    from winduo2.effect.geometry import PROFILE_SAMPLES

    assert native.PROFILE_SAMPLES == PROFILE_SAMPLES


def test_without_the_dll_the_renderer_is_simply_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(native, "_lib", None)
    monkeypatch.setattr(native, "_load_tried", False)
    monkeypatch.setattr(native, "dll_path", lambda: tmp_path / "missing.dll")
    assert not native.available()


needs_dll = pytest.mark.skipif(
    sys.platform != "win32" or not native.dll_path().exists(),
    reason="the native renderer is only built on Windows",
)


@needs_dll
def test_the_dll_loads_and_speaks_this_version():
    assert native.available(), native.last_error()


@needs_dll
def test_shaders_and_pipeline_draw_on_warp():
    # Compiles every shader, scales a grey desktop into the picture, builds the
    # pyramid, draws a flat and a dimmed frame, and reads both back.
    ok, message = native.selftest()
    assert ok, message


class _FakeLib:
    """Records the calls NativeOverlay makes, standing in for the DLL."""

    def __init__(self, has_picture_after: int = 1) -> None:
        self.calls: list[tuple] = []
        self.frames = 0
        self.has_picture_after = has_picture_after

    def wd_frame(self, handle, params, want, timings):
        self.frames += 1
        self.calls.append(("frame", want, params._obj.picture_fade))
        if self.frames < self.has_picture_after:
            return 0
        return native._DREW | native._HAS_PICTURE | (native._NEW_PICTURE if want else 0)

    def wd_opacity(self, handle, start, end, seconds):
        self.calls.append(("opacity", start, end, seconds))
        return 1

    def wd_blank(self, handle):
        self.calls.append(("blank",))

    def wd_clear(self, handle):
        self.calls.append(("clear",))
        return 1

    def wd_hwnd(self, handle):
        return None

    def wd_destroy(self, handle):
        self.calls.append(("destroy",))


_APP = None


@pytest.fixture
def overlay(monkeypatch):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    # Held for the session: a collected application takes the timers with it.
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv[:1])
    made = native.NativeOverlay()
    made._lib = _FakeLib(has_picture_after=2)
    made._handle = 1
    info = native._Info()
    info.screen_width, info.screen_height = 1920, 1200
    info.picture_width, info.picture_height = 1600, 1000
    info.excluded = 1
    made.info = info
    made._shown = True
    return made


def _params():
    from winduo2.effect.geometry import DepthGeometry
    from winduo2.render.overlay import FrameParams

    profile = DepthGeometry().profile(100.0, 70.0, 3.0, 1.0, (1920.0, 1200.0))
    return FrameParams(profile=profile)


def test_fades_in_once_the_first_picture_arrives(overlay):
    overlay.submit(native.PULL, _params())
    assert not any(c[0] == "opacity" for c in overlay._lib.calls)
    overlay.submit(native.PULL, _params())
    assert ("opacity", 0.0, 1.0, overlay.FADE_IN) in overlay._lib.calls
    assert overlay.has_picture and not overlay.needs_picture


def test_blacking_out_presents_at_once(overlay):
    overlay.submit(native.PULL, _params())
    overlay.submit(native.PULL, _params())
    overlay._lib.calls.clear()
    overlay.blank_picture()
    kinds = [c[0] for c in overlay._lib.calls]
    assert kinds == ["blank", "frame"]
    assert overlay._lib.calls[1][1:] == (0, 0.0)  # no new picture, drawn black
    assert overlay.showing_stand_in and overlay.needs_picture


def test_dismissing_fades_out_then_clears(overlay):
    overlay.submit(native.PULL, _params())
    overlay.submit(native.PULL, _params())
    overlay._lib.calls.clear()
    overlay.dismiss(animated=True)
    assert overlay._lib.calls == [("opacity", 1.0, 0.0, overlay.FADE_OUT)]
    assert overlay._clear_after_fade.isActive()
    overlay._clear_after_fade.stop()
    overlay._clear_if_hidden()
    assert ("clear",) in overlay._lib.calls and not overlay.has_picture
