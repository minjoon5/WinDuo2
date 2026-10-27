"""The pointer, moved from the top of the screen into the picture.

Windows draws the pointer after everything else, so it stays sharp above the
overlay however blurred the picture under it is. Neither capture backend
includes it either. While the effect runs the real pointer is hidden, and a
copy of it is drawn into each captured frame, where it blurs, dims and tilts
along with everything else.
"""

from __future__ import annotations

import atexit

import cv2
import numpy as np

from winduo2.log import get_logger
from winduo2.render import win32

log = get_logger("pointer")

__all__ = ["PointerStandIn", "composite"]


def composite(frame: np.ndarray, image: np.ndarray, x: int, y: int) -> np.ndarray:
    """``frame`` with the straight-alpha BGRA ``image`` laid over it at ``x, y``.

    Returns a new array and leaves ``frame`` alone: the capture backend may hand
    the same buffer over again, and drawing into it twice would leave a trail.
    The alpha channel is left as captured.
    """
    height, width = frame.shape[:2]
    image_h, image_w = image.shape[:2]
    left, top = max(x, 0), max(y, 0)
    right, bottom = min(x + image_w, width), min(y + image_h, height)
    if left >= right or top >= bottom:
        return frame

    out = frame.copy()
    region = out[top:bottom, left:right, :3].astype(np.float32)
    piece = image[top - y : bottom - y, left - x : right - x]
    alpha = piece[:, :, 3:4].astype(np.float32) / 255.0
    region = piece[:, :, :3] * alpha + region * (1.0 - alpha)
    out[top:bottom, left:right, :3] = np.clip(region + 0.5, 0, 255).astype(np.uint8)
    return out


class PointerStandIn:
    """Hides the pointer for one run of the effect and draws it into the frames.

    The shape is taken once, as the run starts, since afterwards the system
    cursors are blank. The position is read on every frame, so a pointer moved
    during a close still moves in the picture.
    """

    def __init__(self) -> None:
        self._image: np.ndarray | None = None
        self._hotspot = (0, 0)
        self._hidden = False
        self._guarded = False

    @property
    def is_hidden(self) -> bool:
        return self._hidden

    def begin(self) -> None:
        if self._hidden:
            return
        try:
            taken = win32.pointer_image()
        except Exception:
            log.debug("could not read the pointer", exc_info=True)
            taken = None
        if taken is None:
            # Nothing on screen to move, so nothing to hide.
            return
        self._image, self._hotspot = taken
        self._hidden = win32.hide_pointer()
        if not self._hidden:
            self._image = None
        elif not self._guarded:
            # Blank system cursors outlive the process that set them, so an
            # exit that skips the normal teardown must still put them back.
            atexit.register(self.end)
            self._guarded = True

    def draw(self, frame: np.ndarray, native_size: tuple[int, int]) -> np.ndarray:
        """``frame`` with the pointer in it, scaled to the frame's resolution."""
        if self._image is None:
            return frame
        position = win32.pointer_position()
        if position is None:
            return frame
        native_w = native_size[0]
        scale = frame.shape[1] / native_w if native_w > 0 else 1.0
        image = self._image
        if abs(scale - 1.0) > 1e-3:
            size = (
                max(1, round(image.shape[1] * scale)),
                max(1, round(image.shape[0] * scale)),
            )
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        x = round((position[0] - self._hotspot[0]) * scale)
        y = round((position[1] - self._hotspot[1]) * scale)
        return composite(frame, image, x, y)

    def end(self) -> None:
        if self._hidden:
            win32.restore_pointer()
        self._hidden = False
        self._image = None
