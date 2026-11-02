"""Frames in step with the display, rather than with a millisecond timer.

A ``QTimer`` counts whole milliseconds, so the 1/60 s the effect asked for
became 16 ms: 62.5 frames a second against a 60 Hz panel, which drops or
doubles a frame about every 0.4 s and reads as a small, regular hitch. A timer
also knows nothing of when the compositor takes the next frame, so even a
correct period drifts against it, and a 120 Hz panel was held to 60.

On Windows every window reaches the screen through DWM, and ``DwmFlush``
returns once DWM has composed the next frame. Waiting on it in a thread and
drawing as soon as it returns gives one frame per refresh at whatever rate the
panel runs, with the whole refresh to get the frame to DWM before the one after.
Where there is no DWM the pacer falls back to a timer that keeps an exact
average period by aiming each tick at a deadline instead of a fixed delay.

The pacer only adds ticks. The engine keeps its own timer running as a backstop,
so a display that stops composing, as one does when the lid shuts and the panel
turns off, cannot stall the effect.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from winduo2.log import get_logger

log = get_logger("pacer")

__all__ = ["FramePacer", "next_deadline", "refresh_period"]

#: Refresh rates outside this range are taken as a misreport.
_MIN_HZ = 30.0
_MAX_HZ = 360.0


def refresh_period(hz: float | None) -> float:
    """Seconds per refresh for a reported rate, 60 Hz when it is implausible."""
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
    """Calls ``on_frame`` on the Qt thread once per display refresh while running."""

    _vsync = pyqtSignal()
    _lost_compositor = pyqtSignal()

    def __init__(self, on_frame: Callable[[], None], refresh_hz: float | None = None) -> None:
        super().__init__()
        self._on_frame = on_frame
        self.period = refresh_period(refresh_hz)
        self._running = False

        # DWM path.
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._pending = threading.Event()
        self._thread: threading.Thread | None = None
        self._dwm_ok = sys.platform == "win32"
        self._vsync.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._lost_compositor.connect(
            self._restart_on_timer, Qt.ConnectionType.QueuedConnection
        )

        # Timer path.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._timer_tick)
        self._deadline = 0.0

    # --- Control ---------------------------------------------------------

    def set_refresh_rate(self, hz: float | None) -> None:
        self.period = refresh_period(hz)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def uses_compositor(self) -> bool:
        return self._dwm_ok

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._dwm_ok:
            self._ensure_thread()
            self._pending.clear()
            self._wake.set()
        else:
            self._deadline = time.monotonic()
            self._arm()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._wake.clear()
        self._timer.stop()

    def shutdown(self) -> None:
        self.stop()
        self._quit.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=0.5)

    # --- Delivery on the Qt thread ---------------------------------------

    def _deliver(self) -> None:
        self._pending.clear()
        if self._running:
            self._on_frame()

    def _timer_tick(self) -> None:
        if not self._running:
            return
        self._on_frame()
        if self._running and not self._timer.isActive():
            self._arm()

    def _arm(self) -> None:
        now = time.monotonic()
        self._deadline = next_deadline(self._deadline, self.period, now)
        self._timer.start(max(0, round((self._deadline - now) * 1000)))

    # --- The DWM thread --------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(
            target=self._wait_for_compositor, name="winduo2-vsync", daemon=True
        )
        self._thread.start()

    def _wait_for_compositor(self) -> None:
        import ctypes

        try:
            dwm_flush = ctypes.windll.dwmapi.DwmFlush
        except (AttributeError, OSError):
            self._fall_back("dwmapi is unavailable")
            return

        failures = 0
        while not self._quit.is_set():
            if not self._wake.wait(timeout=1.0):
                continue
            if self._quit.is_set():
                return
            started = time.monotonic()
            # ctypes releases the GIL for the length of the call.
            result = dwm_flush()
            elapsed = time.monotonic() - started
            if result != 0:
                failures += 1
                if failures > 10:
                    self._fall_back(f"DwmFlush failed (0x{result & 0xFFFFFFFF:08x})")
                    return
            else:
                failures = 0
            # A compositor that is not composing, such as with the panel off,
            # can return at once. Never spin faster than the panel refreshes.
            if result != 0 or elapsed < self.period * 0.25:
                time.sleep(max(self.period - elapsed, 0.0))
            if not self._running:
                continue
            # One frame in flight at most. If the Qt thread has not got to the
            # last one yet, this refresh is skipped rather than queued behind it.
            if not self._pending.is_set():
                self._pending.set()
                self._vsync.emit()

    def _fall_back(self, reason: str) -> None:
        log.warning("pacing frames with a timer: %s", reason)
        self._dwm_ok = False
        # Emitted, not called: the timer has to be started on the Qt thread.
        self._lost_compositor.emit()

    def _restart_on_timer(self) -> None:
        if self._running:
            self._deadline = time.monotonic()
            self._arm()
