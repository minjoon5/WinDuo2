"""The pointer, kept off the screen while the effect runs.

Windows draws the pointer after everything else, so it would stay sharp above
the overlay however blurred the picture under it is. While the effect runs the
real pointer is hidden instead, and comes back when the effect ends.
"""

from __future__ import annotations

import atexit

from winduo2.log import get_logger
from winduo2.render import win32

log = get_logger("pointer")

__all__ = ["PointerHider"]


class PointerHider:
    """Hides the pointer for one run of the effect."""

    def __init__(self) -> None:
        self._hidden = False
        self._guarded = False

    @property
    def is_hidden(self) -> bool:
        return self._hidden

    def begin(self) -> None:
        if self._hidden:
            return
        try:
            self._hidden = win32.hide_pointer()
        except Exception:
            log.debug("could not hide the pointer", exc_info=True)
            self._hidden = False
        if self._hidden and not self._guarded:
            # Blank system cursors outlive the process that set them, so an
            # exit that skips the normal teardown must still put them back.
            atexit.register(self.end)
            self._guarded = True

    def end(self) -> None:
        if self._hidden:
            win32.restore_pointer()
        self._hidden = False
