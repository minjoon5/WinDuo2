"""The pointer has to land in the picture where it was on screen, once."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2")

from winduo2.render import pointer  # noqa: E402
from winduo2.render.pointer import PointerStandIn, composite  # noqa: E402


def _arrow(size: int = 4, alpha: int = 255) -> np.ndarray:
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[:, :, :3] = 255
    image[:, :, 3] = alpha
    return image


def test_an_opaque_pointer_replaces_the_pixels_under_it():
    frame = np.zeros((10, 10, 4), dtype=np.uint8)
    out = composite(frame, _arrow(), 3, 2)
    assert (out[2:6, 3:7, :3] == 255).all()
    assert out[:2].sum() == 0 and out[:, :3].sum() == 0


def test_the_captured_frame_is_left_untouched():
    # The backend may hand the same buffer over again; a second draw into it
    # would leave a trail behind a moving pointer.
    frame = np.zeros((10, 10, 4), dtype=np.uint8)
    composite(frame, _arrow(), 0, 0)
    assert frame.sum() == 0


def test_half_alpha_blends():
    frame = np.zeros((4, 4, 4), dtype=np.uint8)
    out = composite(frame, _arrow(alpha=128), 0, 0)
    assert 126 <= out[0, 0, 0] <= 130


def test_alpha_channel_is_left_as_captured():
    frame = np.full((4, 4, 4), 7, dtype=np.uint8)
    out = composite(frame, _arrow(), 0, 0)
    assert (out[:, :, 3] == 7).all()


@pytest.mark.parametrize("x, y", [(-2, -2), (8, 8), (-2, 8)])
def test_a_pointer_at_the_edge_is_clipped(x, y):
    frame = np.zeros((10, 10, 4), dtype=np.uint8)
    out = composite(frame, _arrow(), x, y)
    assert (out[:, :, :3] == 255).sum() == 2 * 2 * 3


def test_a_pointer_off_the_frame_draws_nothing():
    frame = np.zeros((10, 10, 4), dtype=np.uint8)
    assert composite(frame, _arrow(), 20, 20) is frame


class _FakeWin32:
    def __init__(self, image=None, position=(0, 0), hides=True):
        self.image = image
        self.position = position
        self.hides = hides
        self.hidden = 0
        self.restored = 0

    def pointer_image(self):
        return self.image

    def pointer_position(self):
        return self.position

    def hide_pointer(self):
        self.hidden += 1
        return self.hides

    def restore_pointer(self):
        self.restored += 1


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        stub = _FakeWin32(**kwargs)
        monkeypatch.setattr(pointer, "win32", stub)
        monkeypatch.setattr(pointer.atexit, "register", lambda _f: None)
        return stub

    return install


def test_hidden_for_the_run_and_restored_once(fake):
    stub = fake(image=(_arrow(), (1, 1)))
    stand_in = PointerStandIn()
    stand_in.begin()
    stand_in.begin()
    assert stand_in.is_hidden and stub.hidden == 1
    stand_in.end()
    stand_in.end()
    assert not stand_in.is_hidden and stub.restored == 1


def test_no_pointer_on_screen_hides_nothing(fake):
    stub = fake(image=None)
    stand_in = PointerStandIn()
    stand_in.begin()
    stand_in.end()
    assert stub.hidden == 0 and stub.restored == 0


def test_drawn_at_the_hotspot_and_scaled_to_the_frame(fake):
    # A 2000 pixel screen captured at 1000: everything halves.
    fake(image=(_arrow(8), (2, 2)), position=(102, 42))
    stand_in = PointerStandIn()
    stand_in.begin()
    frame = np.zeros((100, 1000, 4), dtype=np.uint8)
    out = stand_in.draw(frame, (2000, 200))
    lit = np.argwhere(out[:, :, 0] == 255)
    assert lit.min(axis=0).tolist() == [20, 50]
    assert lit.max(axis=0).tolist() == [23, 53]


def test_not_drawn_when_hiding_failed(fake):
    fake(image=(_arrow(), (0, 0)), hides=False)
    stand_in = PointerStandIn()
    stand_in.begin()
    frame = np.zeros((10, 10, 4), dtype=np.uint8)
    assert stand_in.draw(frame, (10, 10)) is frame
