"""The pointer, kept off the screen while the effect runs.

Windows draws the pointer after everything else, so it would stay sharp above
the overlay however blurred the picture under it is. While the effect runs the
real pointer is hidden instead, and comes back when the effect ends, or as
soon as the mouse moves: someone reaching for it wants to see it.
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
        self._hidden_at: tuple[int, int] | None = None

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
        self._hidden_at = self._position() if self._hidden else None
        if self._hidden and not self._guarded:
            # Blank system cursors outlive the process that set them, so an
            # exit that skips the normal teardown must still put them back.
            atexit.register(self.end)
            self._guarded = True

    def watch(self) -> None:
        """Give the pointer back early if the mouse has moved since it was hidden.

        Called every frame. Once given back it stays visible for the rest of
        the run, since only :meth:`begin` hides it again.
        """
        if not self._hidden:
            return
        position = self._position()
        if position is None or self._hidden_at is None:
            # Without a starting point a move cannot be told apart from
            # stillness, so record one and compare from the next frame.
            self._hidden_at = position
            return
        if position != self._hidden_at:
            self.end()

    def end(self) -> None:
        if self._hidden:
            win32.restore_pointer()
        self._hidden = False
        self._hidden_at = None

    @staticmethod
    def _position() -> tuple[int, int] | None:
        try:
            return win32.pointer_position()
        except Exception:
            log.debug("could not read the pointer position", exc_info=True)
            return None
