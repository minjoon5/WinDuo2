"""The padded texture has to hold every captured frame exactly."""

from __future__ import annotations

import pytest

pytest.importorskip("OpenGL")

from winduo.render.capture import _capped  # noqa: E402
from winduo.render.overlay import texture_layout  # noqa: E402

SCREENS = [
    (1920, 1080),
    (2560, 1600),
    # Galaxy Book Pro 360: before the fix this came to a 1733 pixel texture
    # for a 1734 pixel need, and every captured frame was dropped.
    (2880, 1800),
    (3000, 2000),
    (3840, 2160),
    (1366, 768),
]


@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("width_cap", [0, 640, 1280, 1600, 1920])
def test_a_captured_frame_fits_with_its_margin(screen, width_cap):
    picture = _capped(screen, width_cap)
    texture, inset, _, _ = texture_layout(screen, picture)
    assert picture[0] + 2 * inset == texture[0]
    assert picture[1] + 2 * inset == texture[1]


@pytest.mark.parametrize("screen", SCREENS)
def test_the_shader_mapping_lands_on_the_picture(screen):
    # Screen point 0 and the far edge must sample the picture's first and last
    # pixel boundaries, or the picture is shifted or stretched inside its margin.
    picture = _capped(screen, 1600)
    texture, inset, origin, padded = texture_layout(screen, picture)
    for axis in (0, 1):
        start = (0.0 - origin[axis]) / padded[axis]
        end = (screen[axis] - origin[axis]) / padded[axis]
        assert start == pytest.approx(inset / texture[axis])
        assert end == pytest.approx((inset + picture[axis]) / texture[axis])
