"""Turns lid angle readings into degrees of travel away from neutral.

Neutral is wherever the lid last held still. When the angle stops changing for
long enough, the current position becomes the new zero, and travel is measured
from there.

The sensor already gives an angle, so the zero is not there to cancel drift. It
is there because the effect is about a close, not about a position: a lid left
partway closed eventually becomes the new neutral, travel collapses back to
zero, and the effect eases out on its own instead of sitting on screen until
the lid moves again. Eventually, not at once: someone pausing mid-close to look
at the screen is still closing, so a lid held closed past ``hold_travel`` keeps
its travel for ``hold_seconds`` before it counts as rest.

When only the lid's accelerometer is available the reading is the lid's tilt
against gravity rather than the hinge angle, and measuring movement from
a resting point is also what makes that work on a lap or a tilted desk.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from winduo.log import get_logger

log = get_logger("estimator")

__all__ = ["AngleSample", "TravelEstimator"]


@dataclass(frozen=True)
class AngleSample:
    """One published estimate."""

    #: Degrees of closing away from neutral. Negative means opened past neutral.
    travel: float
    #: Degrees per second. Positive while closing.
    velocity: float
    #: 0 to 1. Below the configured floor, the effect will not trigger.
    confidence: float
    #: Monotonic timestamp of the reading this came from.
    timestamp: float
    #: True while the lid is considered to be at rest.
    at_rest: bool
    #: The lid angle the sensor reported, 0 shut and 180 flat. Places the eye
    #: for the perspective; the decision to run the effect never reads it.
    absolute_angle: float = 0.0

    @property
    def is_closing(self) -> bool:
        return self.velocity > 0.0


class TravelEstimator:
    """Decides where neutral is, and publishes travel and speed from it.

    Not thread safe. The sensor thread owns it and hands finished samples across.
    """

    #: Readings kept for the least-squares speed fit, in seconds. Short enough
    #: to react, long enough to average away accelerometer noise.
    _VELOCITY_WINDOW = 0.1

    #: Confidence recovers no faster than this per second after a bad patch, so
    #: one lucky reading cannot re-arm the effect.
    _CONFIDENCE_RISE = 4.0
    #: And falls at least this fast, so one bad reading does stop it.
    _CONFIDENCE_FALL = 12.0

    def __init__(
        self,
        settle_seconds: float = 0.4,
        settle_tolerance: float = 1.5,
        hold_seconds: float = 0.4,
        hold_travel: float = 6.0,
    ) -> None:
        self.settle_seconds = settle_seconds
        self.settle_tolerance = settle_tolerance
        self.hold_seconds = hold_seconds
        self.hold_travel = hold_travel
        self._still_since: float | None = None
        self._angle: float | None = None
        self._neutral: float | None = None
        self._at_rest = False
        self._history: deque[tuple[float, float]] = deque()
        self._rest_window: deque[tuple[float, float]] = deque()
        self._confidence = 0.0
        self._last_sample: AngleSample | None = None

    # --- Configuration ---------------------------------------------------

    def retune(
        self,
        settle_seconds: float | None = None,
        settle_tolerance: float | None = None,
        hold_seconds: float | None = None,
        hold_travel: float | None = None,
    ) -> None:
        """Adopt new settings without losing the current neutral reference."""
        if settle_seconds is not None:
            self.settle_seconds = settle_seconds
        if settle_tolerance is not None:
            self.settle_tolerance = settle_tolerance
        if hold_seconds is not None:
            self.hold_seconds = hold_seconds
        if hold_travel is not None:
            self.hold_travel = hold_travel

    def reset(self) -> None:
        """Forget everything. Used on wake and on sensor restart."""
        self._angle = None
        self._neutral = None
        self._at_rest = False
        self._still_since = None
        self._history.clear()
        self._rest_window.clear()
        self._confidence = 0.0
        self._last_sample = None

    # --- Measurement -----------------------------------------------------

    def feed(self, angle: float, confidence: float, now: float) -> AngleSample:
        """Take one reading: the lid angle in degrees and how far to trust it."""
        self._blend_confidence(confidence, now)
        angle = self._unwrap(angle)

        if self._neutral is None:
            self._neutral = angle

        self._update_rest(angle, now)
        travel = self._neutral - angle
        sample = AngleSample(
            travel=travel,
            velocity=self._velocity(angle, now),
            confidence=self._confidence,
            timestamp=now,
            at_rest=self._at_rest,
            absolute_angle=angle % 360.0,
        )
        self._last_sample = sample
        return sample

    def note_dropped_reading(self, now: float) -> AngleSample | None:
        """Called when the sensor gave nothing. Bleeds confidence away.

        A stalled sensor must not leave a stale high-confidence sample standing,
        or the effect can trigger on an angle from a second ago.
        """
        self._confidence = max(0.0, self._confidence - 0.15)
        if self._last_sample is None:
            return None
        sample = AngleSample(
            travel=self._last_sample.travel,
            velocity=0.0,
            confidence=self._confidence,
            timestamp=now,
            at_rest=self._at_rest,
            absolute_angle=self._last_sample.absolute_angle,
        )
        self._last_sample = sample
        return sample

    # --- Internals -------------------------------------------------------

    def _unwrap(self, angle: float) -> float:
        """Keep the angle continuous across the 0/360 seam."""
        if self._angle is not None:
            while angle - self._angle > 180.0:
                angle -= 360.0
            while angle - self._angle < -180.0:
                angle += 360.0
        self._angle = angle
        return angle

    def _blend_confidence(self, target: float, now: float) -> None:
        last = self._last_sample.timestamp if self._last_sample else now
        dt = max(min(now - last, 0.5), 1e-3)
        rate = self._CONFIDENCE_RISE if target > self._confidence else self._CONFIDENCE_FALL
        step = rate * dt
        delta = target - self._confidence
        if abs(delta) <= step:
            self._confidence = target
        else:
            self._confidence += step if delta > 0 else -step

    def _update_rest(self, angle: float, now: float) -> None:
        """Re-zero neutral once the lid has held still long enough.

        Rest is judged by how far the angle ranged over the whole settle window,
        not by how far it has wandered from a single reference point. A
        reference with a displacement budget resets that budget every time it
        trips, so a slow, steady close would re-establish rest over and over and
        drag the zero along behind it, cancelling the effect on exactly the
        movement it exists for. A window has no budget to reset, so any movement
        faster than ``settle_tolerance`` per ``settle_seconds`` can never look
        like rest, however long it goes on.

        Low confidence blocks rest outright. A laptop being carried must not
        redefine neutral to somewhere the lid has never been.

        A lid that stops more than ``hold_travel`` closed from neutral has to
        stay there for ``hold_seconds`` rather than ``settle_seconds``. Measured
        on a Galaxy Book Pro 360: closing at 15 degrees per second and pausing
        to look re-zeroed within 0.4 s, so the effect switched off moments after
        it switched on.
        """
        window = self._rest_window
        window.append((now, angle))
        horizon = now - self.settle_seconds
        while len(window) > 2 and window[0][0] < horizon:
            window.popleft()

        if self._confidence < 0.2:
            window.clear()
            window.append((now, angle))
            self._at_rest = False
            self._still_since = None
            return

        spans_the_window = now - window[0][0] >= self.settle_seconds * 0.9
        if len(window) < 3 or not spans_the_window:
            self._at_rest = False
            self._still_since = None
            return

        values = [value for _, value in window]
        if max(values) - min(values) > self.settle_tolerance:
            self._at_rest = False
            self._still_since = None
            return

        if self._still_since is None:
            self._still_since = window[0][0]
        # Averaging the window rather than taking the newest reading keeps the
        # zero from inheriting one reading's worth of noise.
        centre = sum(values) / len(values)
        held_closed = self._neutral is not None and self._neutral - centre > self.hold_travel
        if held_closed and now - self._still_since < self.hold_seconds:
            self._at_rest = False
            return
        if not self._at_rest:
            log.debug("neutral re-zeroed at %.1f deg", centre)
        self._at_rest = True
        self._neutral = centre

    def _velocity(self, angle: float, now: float) -> float:
        """Least-squares closing speed over a short window, in degrees per second.

        Fitted to the angle rather than to travel. Travel jumps whenever neutral
        re-zeros, and a lid opened 45 degrees past neutral that then comes to
        rest drops its travel from -45 to 0 in one reading: fitted to travel,
        that reads as a 300 degree per second close and starts the effect on a
        lid that was being opened. The angle never jumps.

        A slope beats successive differences here: differencing amplifies the
        sensor's noise, fitting averages it away.
        """
        history = self._history
        # Negated so that closing, which lowers the angle, is positive.
        history.append((now, -angle))
        while len(history) > 3 and history[0][0] < now - self._VELOCITY_WINDOW:
            history.popleft()
        if len(history) < 3:
            return 0.0
        times = np.fromiter((t for t, _ in history), dtype=np.float64)
        values = np.fromiter((v for _, v in history), dtype=np.float64)
        times -= times[0]
        spread = float(((times - times.mean()) ** 2).sum())
        if spread < 1e-9:
            return 0.0
        return float(((times - times.mean()) * (values - values.mean())).sum() / spread)
