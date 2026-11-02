"""The per-run frame summary that goes in the log."""

from __future__ import annotations

from winduo2.render.framestats import FrameStats


def _run(intervals, period=1 / 120):
    stats = FrameStats()
    stats.begin(period)
    now = 0.0
    stats.frame(now, 0.002, 0.004)
    for interval in intervals:
        now += interval
        stats.frame(now, 0.002, None)
        stats.painted(0.001)
        stats.presented(now + 0.003)
    return stats


def test_a_steady_run_misses_nothing():
    stats = _run([1 / 120] * 120)
    assert stats.missed == 0
    line = stats.summary()
    assert "121 frames" in line and "120 fps of 120" in line and "0 missed" in line


def test_an_interval_of_two_periods_is_a_missed_frame():
    stats = _run([1 / 120] * 10 + [2 / 120] + [1 / 120] * 10)
    assert stats.missed == 1


def test_present_is_measured_from_hand_over():
    stats = _run([1 / 120] * 5)
    assert all(abs(p - 0.003) < 1e-9 for p in stats.presents)


def test_nothing_is_recorded_outside_a_run():
    stats = FrameStats()
    stats.frame(1.0, 0.002, None)
    assert stats.work == []
    assert stats.summary() == "0 frame(s)"


def test_summary_ends_the_run():
    stats = _run([1 / 120] * 3)
    stats.summary()
    assert not stats.active
