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
