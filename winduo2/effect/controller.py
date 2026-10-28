"""Decides when the effect runs, and drives it while it does.

Ported from ``LidController.swift`` (Apache 2.0, Copyright 2026 Makito), with
absolute angles replaced by travel from neutral.

One behaviour comes free from that swap and is worth pointing out, because Mac
Duo needs a whole subsystem for it. Its "timeout" setting exists to end the
effect when someone stops the lid partway and leaves it there, since an absolute
angle below the threshold stays below it forever. Here, a lid that stops becomes
the new neutral, travel collapses to zero on its own, and the effect eases back
to flat without anything having to notice. The timeout, its reference angle, its
still-duration, and its awaiting-release flag are all gone.

Opening runs the same effect backwards. A lid opened from below where the last
close started shows the effect at the strength that angle had on the way down,
and it unwinds as the lid rises back to that open angle.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum, auto

from winduo2.angle.estimator import AngleSample
from winduo2.config import Settings
from winduo2.effect.geometry import DEFAULT_NEUTRAL_ANGLE, DepthGeometry
from winduo2.effect.gradient import BlurGradient
from winduo2.effect.spring import CriticallyDampedSpring
from winduo2.log import get_logger

log = get_logger("controller")

__all__ = ["EffectController", "Phase", "Frame", "tilt_knee"]


def tilt_knee(travel: float, limit: float) -> float:
    """Degrees of tilt for ``travel`` degrees of closing past the trigger.

    ``limit * tanh(travel / limit)``: slope 1 at zero and only a cubic
    departure from it, so most of the close tracks the lid and the picture
    holds still in the room, then a smooth approach to ``limit`` so the end of
    a close cannot stretch the picture into a smear. No kink anywhere.
    """
    if travel <= 0.0:
        return 0.0
    if limit <= 0.0:
        return travel
    return limit * math.tanh(travel / limit)


class Phase(Enum):
    #: Nothing on screen. Watching for a close.
    IDLE = auto()
    #: The effect is up and following the lid.
    RUNNING = auto()
    #: Easing back to flat before the overlay fades out.
    CLOSING_OUT = auto()


#: Closing speed that counts as deliberate, in degrees per second. A hand
#: resting on a lid reads under 1.
TRIGGER_SPEED = 4.0

#: How long after the lid last moved downward the effect may still start. A
#: close that pauses for a moment mid-travel should not have to re-earn it.
CLOSING_MEMORY = 1.2

#: How far past neutral the lid has to open before the effect runs backwards,
#: in degrees. Enough to step over accelerometer noise on a resting lid.
OPENING_TRAVEL = 3.0

#: An opening run ends when the lid comes to rest, which re-zeros neutral and
#: collapses the opening travel under this. The estimator's settle tolerance.
OPENING_RELEASE = 1.5

#: Below this the estimate is too noisy to extrapolate usefully.
PREDICTION_SPEED_FLOOR = 30.0

#: Sensor latency the prediction adds on top of the sample's own age: about one
#: report interval of the accelerometer or hinge sensor.
PREDICTION_LATENCY = 0.02

#: The overlay stays up at least this long once shown. Prediction can trigger
#: the effect while the newest sample is still short of the threshold, and a
#: run that appears and vanishes within two frames reads as a glitch.
MINIMUM_DURATION = 0.3

#: How long the picture takes to ease back to flat once the effect ends, in
#: seconds. A timed ease rather than the spring: chasing a target that had just
#: dropped to zero, the spring snapped back in about 0.3 s, which on a Galaxy
#: Book Pro 360 looked abrupt when the lid stopped partway.
CLOSING_OUT_DURATION = 0.6


def ease_in_out(t: float) -> float:
    """Smootherstep: zero speed at both ends, so the return neither jerks into
    motion nor lands with a bump."""
    t = min(max(t, 0.0), 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


IDLE_INTERVAL = 1.0 / 8
ACTIVE_INTERVAL = 1.0 / 60


@dataclass(frozen=True)
class Frame:
    """What the renderer should draw, or ``None`` from :meth:`EffectController.step`."""

    #: Lid angle the effect started at, for the geometry's eye placement.
    start_angle: float
    #: The eased current angle.
    current_angle: float
    #: 0 to 1, driving blur and dimming.
    progress: float
    #: Degrees per second the lid is closing at, straight from the latest
    #: sample rather than eased. Feeds :meth:`BlurGradient.motion_boost`; the
    #: eased travel already smooths the picture's position, so the speed used
    #: for the extra blur should reflect the real motion rather than a second
    #: derivative of something already smoothed once.
    velocity: float = 0.0
    #: True on the frame that finishes the ease back to flat.
    is_final: bool = False
    #: True while the lid is being opened and the effect is running backwards.
    opening: bool = False


class EffectController:
    """Watches travel and decides what the overlay shows.

    Polling is deliberate rather than event-driven, so a stalled sensor thread
    cannot wedge the effect on screen: every decision is re-made from the newest
    sample on a fixed cadence, and a missing sample is itself a decision.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.phase = Phase.IDLE
        self.geometry = DepthGeometry()
        self.gradient = BlurGradient()

        self._travel = CriticallyDampedSpring(0.0)
        self._raw_travel = 0.0
        self._velocity = 0.0
        self._confidence = 0.0
        self._neutral_angle = DEFAULT_NEUTRAL_ANGLE
        self._angle = DEFAULT_NEUTRAL_ANGLE
        # Where the lid stood open before the last close. Opening unwinds the
        # effect toward this angle.
        self._open_angle = DEFAULT_NEUTRAL_ANGLE
        self._opening = False
        self._sample_time = 0.0
        self._last_closing = -1e9
        self._last_opening = -1e9
        self._started_at = 0.0
        self._closing_out_since = 0.0
        self._closing_out_from = 0.0
        self._last_frame_time = 0.0
        self._suspended = False

    # --- Configuration ---------------------------------------------------

    def retune(self, settings: Settings, neutral_angle: float | None = None) -> None:
        self.settings = settings
        if neutral_angle:
            self._neutral_angle = neutral_angle

    def suspend(self) -> None:
        """The machine is sleeping or the screen configuration changed."""
        self._suspended = True
        self._abandon()

    def resume(self) -> None:
        self._suspended = False
        # A fresh baseline, so waking with a nearly shut lid does not read as
        # closing movement.
        self._raw_travel = 0.0
        self._velocity = 0.0
        self._last_closing = -1e9
        self._last_opening = -1e9
        self._travel.reset(0.0)

    @property
    def poll_interval(self) -> float:
        if self.phase is not Phase.IDLE:
            return ACTIVE_INTERVAL
        # Faster polling once the lid is moving at all, so the trigger is not
        # found up to an eighth of a second late.
        if abs(self._velocity) > 1.0 or self._raw_travel > 1.0:
            return ACTIVE_INTERVAL
        return IDLE_INTERVAL

    @property
    def is_active(self) -> bool:
        return self.phase is not Phase.IDLE

    @property
    def travel(self) -> float:
        return self._raw_travel

    @property
    def opening(self) -> bool:
        """The lid is rising from somewhere the effect would show, so a run
        backwards may be about to start."""
        return self._velocity < -1.0 and self._reopen_travel() > self.settings.trigger_travel

    # --- Input -----------------------------------------------------------

    def observe(self, sample: AngleSample | None, now: float | None = None) -> None:
        """Take the newest estimate. ``None`` means the sensor gave nothing."""
        now = time.monotonic() if now is None else now
        if sample is None:
            # An absent sample is not a still lid, it is an unknown one. Dropping
            # confidence to zero releases the effect rather than freezing it on
            # a stale angle.
            self._confidence = 0.0
            self._velocity = 0.0
            return

        self._raw_travel = sample.travel
        self._velocity = sample.velocity
        self._confidence = sample.confidence
        self._sample_time = sample.timestamp
        # Held for the whole run. Neutral re-zeros when a lid stops partway,
        # which moves this by the full travel in one reading; mid-run that
        # moved the eye the perspective is built around, and the picture
        # stretched up the screen before easing back to flat.
        if sample.absolute_angle > 0 and self.phase is Phase.IDLE:
            self._neutral_angle = sample.absolute_angle + sample.travel
        if sample.absolute_angle > 0:
            self._angle = sample.absolute_angle
        else:
            self._angle = self._neutral_angle - sample.travel
        if sample.velocity >= TRIGGER_SPEED:
            self._last_closing = now
        elif sample.velocity <= -TRIGGER_SPEED:
            self._last_opening = now

    def lid_shut(self) -> None:
        """The operating system reported the lid closed. The display is going off."""
        if self.phase is not Phase.IDLE:
            log.info("lid shut, ending the effect")
        self._abandon()

    # --- Decisions -------------------------------------------------------

    def _predicted_travel(self, now: float) -> float:
        """Where the lid is heading, not where the last sample saw it.

        A sensor sample is a report interval old before it arrives, and a lid
        closing at 150 degrees per second covers 3 degrees in that time. Without
        this the effect starts visibly late on a quick close.
        """
        if self._velocity < PREDICTION_SPEED_FLOOR:
            return self._raw_travel
        staleness = min(max(now - self._sample_time, 0.0), 0.12)
        return self._raw_travel + self._velocity * (staleness + PREDICTION_LATENCY)

    def _reopen_travel(self) -> float:
        """How far closed the lid is from where it stood open before the last
        close. Opening drives the effect with this, so each angle on the way up
        shows what it showed on the way down."""
        return self._open_angle - self._angle

    def _target_travel(self) -> float:
        travel = self._reopen_travel() if self._opening else self._raw_travel
        return max(travel, 0.0)

    def _wants_closing(self, now: float) -> bool:
        # A lid resting below the threshold must not start the effect by itself,
        # so a recent downward movement is required as well as the position.
        closing_recently = now - self._last_closing < CLOSING_MEMORY
        return closing_recently and self._predicted_travel(now) >= self.settings.trigger_travel

    def _wants_opening(self, now: float) -> bool:
        # The same rule mirrored: a deliberate upward movement, from an angle
        # where the effect would be showing on the way down.
        opening_recently = now - self._last_opening < CLOSING_MEMORY
        return (
            opening_recently
            and -self._raw_travel >= OPENING_TRAVEL
            and self._reopen_travel() >= self.settings.trigger_travel
        )

    def _wants_effect(self, now: float) -> bool:
        settings = self.settings
        if not settings.enabled or self._suspended:
            return False
        if self._confidence < settings.confidence_floor:
            return False

        if self.phase is Phase.RUNNING:
            if now - self._started_at <= MINIMUM_DURATION:
                return True
            release = settings.trigger_travel - settings.release_hysteresis
            if self._opening:
                # Released once the lid is back up past the threshold, or once
                # it stops partway, which re-zeros neutral under it.
                return -self._raw_travel > OPENING_RELEASE and self._reopen_travel() > release
            # Released on the way back up, with hysteresis so a lid hovering on
            # the threshold does not flicker. Travel collapsing to zero because
            # the lid came to rest goes through this same door.
            return self._raw_travel > release

        return self._wants_closing(now) or self._wants_opening(now)

    def step(self, now: float | None = None) -> Frame | None:
        """Advance one frame. Returns what to draw, or ``None`` for nothing."""
        now = time.monotonic() if now is None else now
        dt = self._delta(now)

        wanted = self._wants_effect(now)

        if self.phase is Phase.IDLE and wanted:
            self._begin(now, opening=not self._wants_closing(now))
        elif self.phase is Phase.RUNNING and not wanted:
            self._begin_closing_out(now)

        if self.phase is Phase.IDLE:
            return None

        if self.phase is not Phase.CLOSING_OUT:
            self._travel.advance(self._target_travel(), dt)
            return self._frame(self._travel.value)

        elapsed = (now - self._closing_out_since) / CLOSING_OUT_DURATION
        if elapsed < 1.0:
            self._travel.reset(self._closing_out_from * (1.0 - ease_in_out(elapsed)))
            return self._frame(self._travel.value)

        # The frame that fades out has to match the screen behind it exactly, so
        # land on zero rather than just short of it.
        self._travel.reset(0.0)
        self.phase = Phase.IDLE
        return self._frame(0.0, is_final=True)

    # --- Frame construction ----------------------------------------------
    #
    # How far the picture tilts is not how far the lid has closed. For the first
    # stretch past the trigger they are equal, which is what makes the picture
    # read as staying put in the room while the glass turns under it. Held all
    # the way to a nearly shut lid, though, an upright sheet projects onto
    # almost-flat glass as a smeared, magnified strip of whatever sat above the
    # hinge. ``tilt_knee`` bends the one into the other: slope 1 at the trigger,
    # levelling off toward ``tilt_limit``, so the tilt settles on its most
    # legible pose while blur and dimming carry the rest of the close.

    def _frame(self, travel: float, is_final: bool = False) -> Frame:
        settings = self.settings
        span = max(settings.full_effect_travel, 1.0)
        past_trigger = max(travel - settings.trigger_travel, 0.0)
        progress = min(past_trigger / span, 1.0)
        # The effect finishes at the end of the ramp and holds there.
        #
        # Without this the perspective kept stretching after blur and dimming
        # had already saturated, because progress is clamped and the geometry's
        # travel was not. Worse, a lid closed well past the ramp drives the eye
        # behind the glass, the projection's depth term hits its own floor, and
        # the far edge collapses: measured on a 1920x1080 screen at the default
        # perspective, the far edge holds 0.53 of the screen width at 85
        # degrees of travel and then falls to 0.09 by 95. That cliff is what
        # reads as the effect overshooting its limits near the end of a close.
        #
        # Clamping here rather than in DepthGeometry keeps the ported geometry
        # honest: it still projects whatever angle it is handed, and the ramp
        # end, which only this class knows, is what decides the last angle worth
        # handing it.
        if travel <= settings.trigger_travel:
            # Short of the trigger the sheet has not turned at all, so this is
            # flat either way; passing travel through lands the last frame of
            # an ease-out exactly on neutral.
            held_travel = travel
        else:
            held_travel = settings.trigger_travel + tilt_knee(
                min(past_trigger, span), settings.tilt_limit
            )
        reference = self._open_angle if self._opening else self._neutral_angle
        return Frame(
            start_angle=reference - settings.trigger_travel,
            current_angle=reference - held_travel,
            progress=progress,
            velocity=self._velocity,
            is_final=is_final,
            opening=self._opening,
        )

    def corners(self, frame: Frame, screen_size: tuple[float, float]):
        """The flat-sheet corners. The renderer uses ``profile``; this stays for
        the outline tests and for anything that only needs the silhouette."""
        return self.geometry.corners(
            start_angle=frame.start_angle,
            current_angle=frame.current_angle,
            viewing_distance_ratio=self.settings.viewing_distance,
            recession=self.settings.recession,
            screen_size=screen_size,
        )

    def profile(self, frame: Frame, screen_size: tuple[float, float]):
        """The per-row projection the shader draws."""
        return self.geometry.profile(
            start_angle=frame.start_angle,
            current_angle=frame.current_angle,
            viewing_distance_ratio=self.settings.viewing_distance,
            recession=self.settings.recession,
            screen_size=screen_size,
        )

    # --- Transitions -----------------------------------------------------

    def _begin(self, now: float, opening: bool = False) -> None:
        self.phase = Phase.RUNNING
        self._started_at = now
        self._opening = opening
        # A close starting from a lid that rests where the effect would already
        # show is the second half of an earlier close, not a fresh one from
        # open, so the open angle stays where that earlier close left it.
        if not opening and self._open_angle - self._neutral_angle < self.settings.trigger_travel:
            self._open_angle = self._neutral_angle
        # Start from where the lid actually is, so the picture does not sweep in
        # from flat after a fast close has already passed the threshold.
        self._travel.reset(self._target_travel())
        log.info(
            "effect on%s: travel %.1f deg, predicted %.1f, %.0f deg/s, confidence %.2f",
            " (opening)" if opening else "",
            self._target_travel(),
            self._predicted_travel(now),
            self._velocity,
            self._confidence,
        )

    def _begin_closing_out(self, now: float) -> None:
        self.phase = Phase.CLOSING_OUT
        self._closing_out_since = now
        self._closing_out_from = self._travel.value
        log.info("effect easing out: travel %.1f deg", self._raw_travel)

    def _abandon(self) -> None:
        """Stop now, with no ease and no fade. For sleep and for a shut lid."""
        self.phase = Phase.IDLE
        self._travel.reset(0.0)
        self._last_closing = -1e9
        self._last_opening = -1e9

    def _delta(self, now: float) -> float:
        last, self._last_frame_time = self._last_frame_time, now
        if last <= 0.0:
            return ACTIVE_INTERVAL
        # Clamped both ways: a long stall must not make the spring explode, and
        # a zero step must not divide by nothing.
        return min(max(now - last, 1.0 / 240), 1.0 / 20)
