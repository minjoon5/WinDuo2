"""Frame pacing: an exact average period, and no burst after a stall."""

from __future__ import annotations

import sys
import time

import pytest

from winduo2.render.pacer import next_deadline, refresh_period


def test_refresh_period_is_the_panels_own():
    assert refresh_period(59.94) == pytest.approx(1 / 59.94)
    assert refresh_period(120.0) == pytest.approx(1 / 120)
    assert refresh_period(144.0) == pytest.approx(1 / 144)


@pytest.mark.parametrize("hz", [None, 0.0, 5.0, 1000.0])
def test_refresh_period_falls_back_to_60_for_nonsense(hz):
    assert refresh_period(hz) == pytest.approx(1 / 60)


def test_deadlines_keep_the_exact_period_despite_millisecond_waits():
    period = 1 / 60
    deadline = 0.0
    now = 0.0
    for _ in range(600):
        deadline = next_deadline(deadline, period, now)
        # The timer only waits whole milliseconds and wakes a little late.
        now = deadline + 0.0007
    # Ten seconds of 60 Hz is 600 frames, not the 625 a 16 ms timer gives.
    assert deadline == pytest.approx(10.0, abs=1e-6)


def test_a_stall_restarts_from_now_instead_of_catching_up():
    period = 1 / 60
    assert next_deadline(1.0, period, 1.5) == pytest.approx(1.5 + period)


def test_a_slightly_late_tick_keeps_the_schedule():
    period = 1 / 60
    assert next_deadline(1.0, period, 1.0 + period + 0.003) == pytest.approx(1.0 + period)


def test_timer_pacer_ticks_at_about_the_refresh_rate():
    pytest.importorskip("PyQt6")
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

    from winduo2.render.pacer import FramePacer

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    ticks: list[float] = []
    pacer = FramePacer(lambda: ticks.append(time.monotonic()), refresh_hz=60.0)
    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    pacer.start()
    loop.exec()
    pacer.shutdown()
    del app
    # 30 at 60 Hz in half a second; leave room for a loaded CI machine.
    assert 20 <= len(ticks) <= 32
