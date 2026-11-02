"""Frames at an exact rate, rather than at whatever a millisecond timer rounds to.

A ``QTimer`` counts whole milliseconds, so the 1/60 s the effect asked for
became 16 ms: 62.5 frames a second against a 60 Hz panel, which drops or
doubles a frame about every 0.4 s and reads as a small, regular hitch. The
pacer aims each tick at a deadline a whole period after the last one instead
of waiting a fixed delay, so the millisecond rounding averages out and the
rate is exact.

The rate is the panel's own, so a 120 Hz panel gets 120 frames a second. How
well a machine keeps up is in the log after every run (see ``framestats``).

An earlier version woke on ``DwmFlush`` to follow DWM's own frames. It could
not be checked on real hardware and the effect juddered badly with it, so it
was taken out.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, QTimer

__all__ = ["FramePacer", "next_deadline", "refresh_period"]

#: Refresh rates outside this range are taken as a misreport.
_MIN_HZ = 30.0
_MAX_HZ = 360.0


def refresh_period(hz: float | None) -> float:
    """Seconds per frame for a reported panel rate, 60 Hz when it is implausible."""
    if not hz or hz < _MIN_HZ or hz > _MAX_HZ:
        hz = 60.0
    return 1.0 / hz


def next_deadline(previous: float, period: float, now: float) -> float:
    """When the tick after one aimed at ``previous`` should land.

    Stepping from the previous deadline rather than from now keeps the average
    period exact even though each wait is rounded to a millisecond. A loop that
    has fallen a whole period or more behind starts again from now rather than
    firing a burst of ticks to catch up.
    """
    target = previous + period
    if target <= now - period or target > now + 2 * period:
        return now + period
    return target


class FramePacer(QObject):
    """Calls ``on_frame`` on the Qt thread once per period while running."""

    def __init__(self, on_frame: Callable[[], None], refresh_hz: float | None = None) -> None:
        super().__init__()
        self._on_frame = on_frame
        self.period = refresh_period(refresh_hz)
        self._running = False
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def set_refresh_rate(self, hz: float | None) -> None:
        self.period = refresh_period(hz)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._deadline = time.monotonic()
        self._arm()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def shutdown(self) -> None:
        self.stop()

    def _tick(self) -> None:
        if not self._running:
            return
        self._on_frame()
        if self._running and not self._timer.isActive():
            self._arm()

    def _arm(self) -> None:
        now = time.monotonic()
        self._deadline = next_deadline(self._deadline, self.period, now)
        self._timer.start(max(0, round((self._deadline - now) * 1000)))
