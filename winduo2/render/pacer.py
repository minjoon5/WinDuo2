"""Frames at an exact rate, rather than at whatever a millisecond timer rounds to.

A ``QTimer`` counts whole milliseconds, so the 1/60 s the effect asked for
became 16 ms: 62.5 frames a second against a 60 Hz panel, which drops or
doubles a frame about every 0.4 s and reads as a small, regular hitch. The
pacer aims each tick at a deadline a whole period after the last one instead
of waiting a fixed delay, so the millisecond rounding averages out and the
rate is exact.

The rate is the panel's own, so a 120 Hz panel gets 120 frames a second. How
well a machine keeps up is in the log after every run (see ``framestats``).

With the native renderer the pacer follows the swap chain instead (see
``set_vsync_source``). An earlier version woke on ``DwmFlush``; it could not
be checked on real hardware and the effect juddered with it, so it went.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

__all__ = ["FramePacer", "VsyncSource", "next_deadline", "refresh_period"]

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


class VsyncSource(Protocol):
    """Something that says when to draw, such as a swap chain's waitable."""

    def wait(self, timeout_ms: int) -> int:
        """1 when a frame is wanted, 2 if an earlier signal is unspent, 0 on timeout."""


#: How long a vsync wait may block before the pacer gives up on it for this
#: tick, as it must when the display turns off and the signal stops.
VSYNC_TIMEOUT_MS = 50


class FramePacer(QObject):
    """Calls ``on_frame`` on the Qt thread once per frame while running.

    Paced by a deadline timer, or, given a ``VsyncSource``, by the display
    itself: a thread waits on the source and hands each signal to the Qt
    thread. The native renderer's swap chain signals just after every vertical
    blank, so frames land on the display's own rhythm at whatever rate it runs,
    and presenting never has to wait.
    """

    _vsync = pyqtSignal()

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

        # The vsync path. The lock is held for the length of every wait, so
        # set_vsync_source(None) returning means no wait is still running on
        # the old source, and its owner may close it.
        self._source: VsyncSource | None = None
        self._source_lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._pending = threading.Event()
        self._thread: threading.Thread | None = None
        self._vsync.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def set_refresh_rate(self, hz: float | None) -> None:
        self.period = refresh_period(hz)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def paced_by_vsync(self) -> bool:
        return self._source is not None

    def set_vsync_source(self, source: VsyncSource | None) -> None:
        """Follow ``source`` from now on, or the timer if None.

        Returns only once no wait on the previous source is under way.
        """
        with self._source_lock:
            self._source = source
        if not self._running:
            return
        if source is None:
            self._wake.clear()
            self._deadline = time.monotonic()
            self._arm()
        else:
            self._timer.stop()
            self._start_thread()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._source is not None:
            self._start_thread()
        else:
            self._deadline = time.monotonic()
            self._arm()

    def stop(self) -> None:
        self._running = False
        self._wake.clear()
        self._timer.stop()

    def shutdown(self) -> None:
        self.stop()
        self._quit.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        with self._source_lock:
            self._source = None

    def _tick(self) -> None:
        if not self._running or self._source is not None:
            return
        self._on_frame()
        if self._running and not self._timer.isActive() and self._source is None:
            self._arm()

    def _arm(self) -> None:
        now = time.monotonic()
        self._deadline = next_deadline(self._deadline, self.period, now)
        self._timer.start(max(0, round((self._deadline - now) * 1000)))

    # --- The vsync path ----------------------------------------------------

    def _start_thread(self) -> None:
        self._pending.clear()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(
            target=self._follow_vsync, name="winduo2-vsync", daemon=True
        )
        self._thread.start()

    def _deliver(self) -> None:
        self._pending.clear()
        if self._running and self._source is not None:
            self._on_frame()

    def _follow_vsync(self) -> None:
        while not self._quit.is_set():
            if not self._wake.wait(timeout=0.5):
                continue
            started = time.monotonic()
            with self._source_lock:
                source = self._source
                result = source.wait(VSYNC_TIMEOUT_MS) if source is not None else 0
            if self._quit.is_set():
                return
            if source is None or not self._running:
                # Stopped, or handed back to the timer: idle until woken.
                self._wake.clear()
                continue
            if result != 1:
                # No fresh signal: the last one is still unspent because that
                # tick drew nothing, or the display stopped signalling. Keep
                # time by the clock rather than spinning or stopping.
                remaining = self.period - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
            # One tick in flight at most; a signal the Qt thread has not got to
            # yet is not queued behind it.
            if not self._pending.is_set():
                self._pending.set()
                self._vsync.emit()
