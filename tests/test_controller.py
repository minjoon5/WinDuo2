"""The effect state machine, stepped on a synthetic clock."""

from __future__ import annotations

import pytest

from winduo2.angle.estimator import AngleSample
from winduo2.config import Settings
from winduo2.effect.controller import (
    CLOSING_MEMORY,
    MINIMUM_DURATION,
    REOPEN_GRACE,
    EffectController,
    Phase,
)

STEP = 1 / 60


def sample(
    travel: float, velocity: float = 0.0, confidence: float = 0.9, now: float = 0.0
) -> AngleSample:
    return AngleSample(
        travel=travel,
        velocity=velocity,
        confidence=confidence,
        timestamp=now,
        at_rest=velocity == 0.0,
        absolute_angle=100.0 - travel,
    )


class Harness:
    """A controller plus a clock, so every test advances time the same way."""

    def __init__(self, **overrides) -> None:
        settings = Settings(**overrides)
        self.controller = EffectController(settings)
        self.now = 0.0
        self.frame = None

    def run(
        self,
        travel: float,
        velocity: float = 0.0,
        confidence: float = 0.9,
        frames: int = 1,
    ):
        for _ in range(frames):
            self.now += STEP
            self.controller.observe(
                sample(travel, velocity, confidence, self.now), self.now
            )
            self.frame = self.controller.step(self.now)
        return self.frame

    def blind(self, frames: int = 1):
        """Frames where the camera gave nothing."""
        for _ in range(frames):
            self.now += STEP
            self.controller.observe(None, self.now)
            self.frame = self.controller.step(self.now)
        return self.frame

    def close(self, to: float, rate: float = 60.0, confidence: float = 0.9):
        """Sweep travel up to ``to`` at ``rate`` degrees per second."""
        travel = self.controller.travel
        while travel < to:
            travel = min(travel + rate * STEP, to)
            self.run(travel, velocity=rate, confidence=confidence)
        return self.frame


class TestTriggering:
    def test_starts_when_a_close_passes_the_threshold(self):
        harness = Harness(trigger_travel=8.0)
        assert harness.controller.phase is Phase.IDLE
        harness.close(to=12.0)
        assert harness.controller.phase is Phase.RUNNING
        assert harness.frame is not None

    def test_does_not_start_below_the_threshold(self):
        # Deliberately slower than the prediction floor. At 60 degrees per
        # second the extrapolation would correctly conclude that a lid at 5
        # degrees is past an 8 degree threshold by the time a frame is drawn,
        # which is tested separately.
        harness = Harness(trigger_travel=8.0)
        harness.close(to=5.0, rate=20.0)
        assert harness.controller.phase is Phase.IDLE
        assert harness.frame is None

    def test_a_lid_parked_past_the_threshold_does_not_start_by_itself(self):
        # Without this, any lid sitting below the trigger would switch the
        # effect on the moment the app launched.
        harness = Harness(trigger_travel=8.0)
        harness.run(20.0, velocity=0.0, frames=30)
        assert harness.controller.phase is Phase.IDLE

    def test_a_close_that_pauses_briefly_can_still_trigger(self):
        harness = Harness(trigger_travel=8.0)
        harness.run(5.0, velocity=40.0, frames=3)
        # Pause well inside the closing memory, then resume.
        harness.run(5.0, velocity=0.0, frames=int(CLOSING_MEMORY * 0.5 / STEP))
        harness.run(12.0, velocity=0.0)
        assert harness.controller.phase is Phase.RUNNING

    def test_a_close_that_stalls_too_long_has_to_earn_it_again(self):
        harness = Harness(trigger_travel=8.0)
        harness.run(5.0, velocity=40.0, frames=3)
        harness.run(5.0, velocity=0.0, frames=int(CLOSING_MEMORY * 1.5 / STEP))
        harness.run(12.0, velocity=0.0)
        assert harness.controller.phase is Phase.IDLE

    def test_disabled_never_starts(self):
        harness = Harness(enabled=False, trigger_travel=8.0)
        harness.close(to=30.0)
        assert harness.controller.phase is Phase.IDLE

    def test_low_confidence_never_starts(self):
        harness = Harness(trigger_travel=8.0, confidence_floor=0.5)
        harness.close(to=30.0, confidence=0.2)
        assert harness.controller.phase is Phase.IDLE

    def test_prediction_triggers_early_on_a_fast_close(self):
        # A fast close should trigger from where the lid is heading. The sample
        # says 6 degrees, short of the 8 degree threshold, but at 200 degrees per
        # second it is already past it by the time anything is drawn.
        harness = Harness(trigger_travel=8.0)
        harness.run(6.0, velocity=200.0)
        assert harness.controller.phase is Phase.RUNNING

    def test_prediction_does_not_fire_on_a_slow_close(self):
        harness = Harness(trigger_travel=8.0)
        harness.run(6.0, velocity=10.0, frames=5)
        assert harness.controller.phase is Phase.IDLE


class TestRelease:
    def test_reopening_past_the_hysteresis_releases(self):
        harness = Harness(trigger_travel=8.0, release_hysteresis=4.0)
        harness.close(to=20.0)
        assert harness.controller.phase is Phase.RUNNING
        harness.run(20.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        harness.run(3.0)
        assert harness.controller.phase is Phase.CLOSING_OUT

    def test_hovering_on_the_threshold_does_not_flicker(self):
        harness = Harness(trigger_travel=8.0, release_hysteresis=4.0)
        harness.close(to=20.0)
        harness.run(20.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        # Just below the trigger but inside the hysteresis band.
        harness.run(6.0, frames=10)
        assert harness.controller.phase is Phase.RUNNING

    def test_a_lid_coming_to_rest_ends_the_effect_on_its_own(self):
        # The estimator re-zeros neutral when the lid stops, so travel collapses
        # to zero without the controller needing a timeout at all. This is the
        # behaviour Mac Duo needs a whole subsystem for.
        harness = Harness(trigger_travel=8.0)
        harness.close(to=25.0)
        assert harness.controller.phase is Phase.RUNNING
        harness.run(25.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        harness.run(0.0, frames=5)
        assert harness.controller.phase is Phase.CLOSING_OUT

    def test_the_effect_stays_up_for_a_minimum_duration(self):
        # Prediction can trigger while the newest sample is still short of the
        # threshold, and a run that vanishes two frames later reads as a glitch.
        harness = Harness(trigger_travel=8.0)
        harness.run(6.0, velocity=200.0)
        assert harness.controller.phase is Phase.RUNNING
        harness.run(0.0, velocity=0.0)
        assert harness.controller.phase is Phase.RUNNING

    def test_losing_the_camera_releases_rather_than_freezing(self):
        harness = Harness(trigger_travel=8.0)
        harness.close(to=25.0)
        harness.run(25.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        harness.blind(3)
        assert harness.controller.phase is Phase.CLOSING_OUT

    def test_a_shut_lid_holds_the_effect_for_the_reopen(self):
        # The display comes back blurred and clears as the lid opens, rather
        # than coming back sharp and blurring once the sensor catches up.
        harness = Harness(trigger_travel=8.0)
        harness.close(to=25.0)
        harness.controller.lid_shut()
        frame = harness.controller.step(harness.now)
        assert harness.controller.phase is Phase.RUNNING
        assert frame.opening and frame.progress == pytest.approx(1.0)

    def test_suspending_stops_everything_at_once(self):
        harness = Harness(trigger_travel=8.0)
        harness.close(to=25.0)
        harness.controller.suspend()
        assert harness.controller.phase is Phase.IDLE
        assert harness.controller.step(harness.now) is None


class TestEasingOut:
    def test_eases_back_to_flat_and_reports_a_final_frame(self):
        harness = Harness(trigger_travel=8.0)
        harness.close(to=30.0)
        harness.run(30.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        harness.run(0.0)
        assert harness.controller.phase is Phase.CLOSING_OUT

        seen = []
        for _ in range(200):
            harness.now += STEP
            frame = harness.controller.step(harness.now)
            if frame is None:
                break
            seen.append(frame)
            if frame.is_final:
                break

        assert seen
        assert seen[-1].is_final
        assert seen[-1].progress == 0.0
        assert seen[-1].current_angle == pytest.approx(seen[-1].start_angle + 8.0)
        # Monotonic, so the picture does not bounce as it flattens.
        progress = [frame.progress for frame in seen]
        assert progress == sorted(progress, reverse=True)

    def test_the_final_frame_lands_exactly_flat(self):
        # A frame that fades out while still slightly warped reveals a mismatch
        # against the real screen behind it.
        harness = Harness(trigger_travel=8.0)
        harness.close(to=30.0)
        harness.run(30.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        harness.run(0.0)
        final = None
        for _ in range(300):
            harness.now += STEP
            frame = harness.controller.step(harness.now)
            if frame is not None and frame.is_final:
                final = frame
                break
        assert final is not None
        assert final.progress == 0.0

    def test_gives_up_easing_after_the_timeout(self):
        harness = Harness(trigger_travel=8.0)
        harness.close(to=30.0)
        harness.run(30.0, frames=int(MINIMUM_DURATION / STEP) + 2)
        harness.run(0.0)
        # Jump the clock past the safety net.
        harness.now += 5.0
        frame = harness.controller.step(harness.now)
        assert frame is not None and frame.is_final
        assert harness.controller.phase is Phase.IDLE


class TestFrames:
    def test_progress_reaches_full_at_the_end_of_the_span(self):
        harness = Harness(trigger_travel=8.0, full_effect_travel=30.0)
        harness.close(to=8.0 + 30.0, rate=200.0)
        # Let the spring catch up to the target.
        harness.run(38.0, frames=60)
        assert harness.frame is not None
        assert harness.frame.progress == pytest.approx(1.0, abs=0.02)

    def test_the_frame_carries_the_sample_velocity(self):
        # The one line that makes the velocity-aware blur work at all. Without
        # it the boost reads a constant zero and the feature is inert.
        harness = Harness(trigger_travel=8.0)
        harness.close(to=8.0)
        harness.run(20.0, velocity=140.0)
        assert harness.frame is not None
        assert harness.frame.velocity == pytest.approx(140.0)

    def test_progress_is_zero_at_the_trigger(self):
        harness = Harness(trigger_travel=8.0, full_effect_travel=30.0)
        harness.run(8.0, velocity=60.0)
        assert harness.frame is not None
        assert harness.frame.progress == pytest.approx(0.0, abs=1e-6)

    def test_progress_never_exceeds_full(self):
        harness = Harness(trigger_travel=8.0, full_effect_travel=20.0)
        harness.close(to=120.0, rate=300.0)
        harness.run(120.0, frames=60)
        assert harness.frame is not None
        assert harness.frame.progress == 1.0

    def test_corners_stay_finite_across_a_whole_sweep(self):
        harness = Harness(trigger_travel=8.0)
        screen = (1920.0, 1080.0)
        harness.close(to=8.0)
        for travel in range(8, 100):
            harness.run(float(travel), velocity=60.0)
            assert harness.frame is not None
            corners = harness.controller.corners(harness.frame, screen)
            assert len(corners) == 4
            for x, y in corners:
                assert -1e5 < x < 1e5
                assert -1e5 < y < 1e5

    def test_the_hinge_edge_stays_pinned_across_a_whole_sweep(self):
        harness = Harness(trigger_travel=8.0)
        screen = (1920.0, 1080.0)
        harness.close(to=8.0)
        for travel in range(8, 90):
            harness.run(float(travel), velocity=60.0)
            assert harness.frame is not None
            corners = harness.controller.corners(harness.frame, screen)
            assert corners[0] == pytest.approx((0.0, 0.0), abs=1e-6)
            assert corners[1] == pytest.approx((1920.0, 0.0), abs=1e-6)

    def test_the_picture_stops_receding_at_the_end_of_the_ramp(self):
        # Progress is clamped but the geometry's travel used not to be, so the
        # perspective kept stretching after blur and dimming had saturated, and
        # closing far enough drove the eye behind the glass and collapsed the
        # far edge to a sliver. Everything past the ramp end must be identical.
        harness = Harness(trigger_travel=8.0, full_effect_travel=40.0)
        screen = (1920.0, 1080.0)
        harness.close(to=8.0)

        def far_edge(travel: float) -> float:
            harness.run(travel, velocity=60.0, frames=90)
            assert harness.frame is not None
            corners = harness.controller.corners(harness.frame, screen)
            return corners[2][0] - corners[3][0]

        at_ramp_end = far_edge(48.0)
        assert at_ramp_end == pytest.approx(far_edge(70.0), rel=1e-6)
        assert at_ramp_end == pytest.approx(far_edge(110.0), rel=1e-6)
        # And it is a real perspective at that point, not a collapsed one.
        assert 0.3 * 1920.0 < at_ramp_end < 0.95 * 1920.0

    def test_the_preview_sweep_reaches_the_same_end_as_a_real_close(self):
        # The preview is the only way most people will judge the look, so it
        # has to arrive at the state a real close arrives at, not short of it.
        from winduo2.app import PreviewSweep

        settings = Settings(trigger_travel=8.0, full_effect_travel=40.0)
        sweep = PreviewSweep(settings.trigger_travel, settings.full_effect_travel)
        ramp_end = settings.trigger_travel + settings.full_effect_travel
        assert sweep.deep >= ramp_end

        harness = Harness(trigger_travel=8.0, full_effect_travel=40.0)
        harness.close(to=8.0)
        harness.run(sweep.deep, velocity=sweep.velocity, frames=120)
        assert harness.frame is not None
        assert harness.frame.progress == pytest.approx(1.0, abs=1e-6)


class TestPolling:
    def test_polls_slowly_while_nothing_is_happening(self):
        harness = Harness()
        harness.run(0.0, frames=5)
        assert harness.controller.poll_interval > 1 / 20

    def test_speeds_up_as_soon_as_the_lid_moves(self):
        harness = Harness()
        harness.run(0.5, velocity=3.0)
        assert harness.controller.poll_interval <= 1 / 60 + 1e-9

    def test_polls_fast_while_the_effect_is_running(self):
        harness = Harness(trigger_travel=8.0)
        harness.close(to=20.0)
        assert harness.controller.poll_interval <= 1 / 60 + 1e-9


class TestSuspension:
    def test_suspending_takes_the_effect_down(self):
        harness = Harness(trigger_travel=8.0)
        harness.close(to=20.0)
        harness.controller.suspend()
        assert harness.controller.phase is Phase.IDLE
        harness.close(to=40.0)
        assert harness.controller.phase is Phase.IDLE

    def test_resuming_does_not_read_a_shut_lid_as_a_close(self):
        harness = Harness(trigger_travel=8.0)
        harness.controller.suspend()
        harness.controller.resume()
        harness.run(40.0, velocity=0.0, frames=5)
        assert harness.controller.phase is Phase.IDLE


class TestNeutralAngleDuringARun:
    def test_a_re_zero_mid_run_does_not_move_the_perspective(self):
        # Measured on a Galaxy Book: the lid stopped partway, neutral re-zeroed
        # from 120 to 90 degrees, and the picture stretched up before easing
        # out. Every frame of a run has to share one start angle.
        controller = EffectController(Settings())
        now = 0.0
        frames = []
        angle = 120.0
        for _ in range(40):
            now += 1 / 60
            angle -= 0.5
            controller.observe(
                AngleSample(
                    travel=120.0 - angle, velocity=30.0, confidence=1.0,
                    timestamp=now, at_rest=False, absolute_angle=angle,
                ),
                now,
            )
            frame = controller.step(now)
            if frame is not None:
                frames.append(frame)
        # The lid stops at 100 and that becomes neutral: travel falls to zero.
        for _ in range(120):
            now += 1 / 60
            controller.observe(
                AngleSample(
                    travel=0.0, velocity=0.0, confidence=1.0,
                    timestamp=now, at_rest=True, absolute_angle=angle,
                ),
                now,
            )
            frame = controller.step(now)
            if frame is not None:
                frames.append(frame)
        assert frames and frames[-1].is_final
        assert {round(f.start_angle, 6) for f in frames} == {round(frames[0].start_angle, 6)}
        # And the ease back never tilts further than where it started from.
        separations = [f.start_angle - f.current_angle for f in frames]
        peak = separations.index(max(separations))
        assert all(b <= a + 1e-9 for a, b in zip(separations[peak:], separations[peak + 1:], strict=False))


class TestOpening:
    """Opening runs the effect backwards, from where the last close started."""

    @staticmethod
    def open_from(harness: Harness, angle: float, to: float, rate: float = 60.0):
        """Rest at ``angle``, then open toward ``to``. Returns every frame."""
        controller = harness.controller
        neutral = angle
        frames = []
        for _ in range(30):
            harness.now += STEP
            controller.observe(
                AngleSample(
                    travel=0.0, velocity=0.0, confidence=0.9,
                    timestamp=harness.now, at_rest=True, absolute_angle=angle,
                ),
                harness.now,
            )
            frame = controller.step(harness.now)
            if frame is not None:
                frames.append(frame)
        while angle < to:
            angle = min(angle + rate * STEP, to)
            harness.now += STEP
            controller.observe(
                AngleSample(
                    travel=neutral - angle, velocity=-rate, confidence=0.9,
                    timestamp=harness.now, at_rest=False, absolute_angle=angle,
                ),
                harness.now,
            )
            frame = controller.step(harness.now)
            if frame is not None:
                frames.append(frame)
        return frames

    @staticmethod
    def shut(harness: Harness) -> None:
        """Close all the way and have the lid switch report it."""
        harness.close(to=95.0)
        harness.controller.lid_shut()

    @staticmethod
    def dropped(harness: Harness) -> None:
        """Close to nearly shut, then lose the run without the lid switch."""
        harness.close(to=95.0)
        harness.controller.suspend()
        harness.controller.resume()

    def test_a_shut_lid_holds_the_effect_at_full_strength(self):
        harness = Harness(trigger_travel=10.0, full_effect_travel=75.0)
        self.shut(harness)
        # Asleep, the sensor reports nothing at all.
        frame = harness.blind(frames=600)
        assert harness.controller.phase is Phase.RUNNING
        assert frame.progress == pytest.approx(1.0)

    def test_opening_from_shut_goes_from_blurred_to_clear(self):
        harness = Harness(trigger_travel=10.0, full_effect_travel=75.0)
        self.shut(harness)
        harness.controller.lid_opened(harness.now)
        frames = self.open_from(harness, 5.0, 100.0)
        assert all(frame.opening for frame in frames)
        # The first frame back is fully blurred, and it only ever clears.
        assert frames[0].progress == pytest.approx(1.0)
        progress = [frame.progress for frame in frames]
        assert all(b <= a + 1e-9 for a, b in zip(progress, progress[1:], strict=False))
        assert harness.controller.phase is not Phase.RUNNING

    def test_rough_readings_after_a_wake_cannot_make_it_blurrier(self):
        harness = Harness(trigger_travel=10.0, full_effect_travel=75.0)
        self.shut(harness)
        harness.controller.lid_opened(harness.now)
        self.open_from(harness, 5.0, 60.0)
        before = harness.controller._travel.value
        # One reading claims the lid fell back to nearly shut.
        harness.now += STEP
        harness.controller.observe(
            AngleSample(
                travel=50.0, velocity=0.0, confidence=0.9,
                timestamp=harness.now, at_rest=False, absolute_angle=10.0,
            ),
            harness.now,
        )
        harness.controller.step(harness.now)
        assert harness.controller._travel.value <= before + 1e-9

    def test_an_open_lid_the_sensor_never_sees_lets_go(self):
        harness = Harness(trigger_travel=10.0)
        self.shut(harness)
        harness.controller.lid_opened(harness.now)
        harness.blind(frames=int(REOPEN_GRACE / STEP) + 2)
        assert harness.controller.phase is not Phase.RUNNING

    def test_shutting_with_nothing_on_screen_starts_nothing(self):
        harness = Harness(trigger_travel=10.0)
        harness.controller.lid_shut()
        assert harness.controller.phase is Phase.IDLE

    def test_each_angle_shows_what_it_showed_on_the_way_down(self):
        harness = Harness(trigger_travel=10.0, full_effect_travel=75.0)
        self.dropped(harness)
        frames = self.open_from(harness, 5.0, 100.0, rate=30.0)
        # 100 open, 60 is 40 degrees closed: 30 past the trigger of a 75 ramp.
        at_60 = min(frames, key=lambda f: abs(f.current_angle - 60.0))
        assert at_60.progress == pytest.approx(30.0 / 75.0, abs=0.05)

    def test_opening_from_open_does_nothing(self):
        harness = Harness(trigger_travel=10.0)
        assert self.open_from(harness, 100.0, 130.0) == []
        assert harness.controller.phase is Phase.IDLE

    def test_a_slow_drift_open_does_not_trigger(self):
        harness = Harness(trigger_travel=10.0)
        self.dropped(harness)
        assert self.open_from(harness, 5.0, 40.0, rate=2.0) == []

    def test_a_lid_stopping_partway_open_ends_the_effect(self):
        harness = Harness(trigger_travel=10.0)
        self.dropped(harness)
        self.open_from(harness, 5.0, 50.0)
        assert harness.controller.phase is Phase.RUNNING
        # The estimator re-zeros neutral at 50, so opening travel collapses.
        harness.now += 1.0
        controller = harness.controller
        controller.observe(
            AngleSample(
                travel=0.0, velocity=0.0, confidence=0.9,
                timestamp=harness.now, at_rest=True, absolute_angle=50.0,
            ),
            harness.now,
        )
        controller.step(harness.now)
        assert controller.phase is Phase.CLOSING_OUT

    def test_a_second_close_from_partway_keeps_the_open_angle(self):
        # Closing from 100 to 60, resting, then on to shut: the lid was open at
        # 100, not at 60, so opening unwinds all the way to 100.
        harness = Harness(trigger_travel=10.0, full_effect_travel=75.0)
        harness.close(to=40.0)
        harness.controller._neutral_angle = 60.0
        harness.controller.phase = Phase.IDLE
        harness.controller._begin(harness.now)
        assert harness.controller._open_angle == 100.0


class TestEaseOut:
    def test_the_return_eases_in_and_out_over_the_set_time(self):
        # The spring snapped back in about 0.3 s and looked abrupt. The return
        # now starts gently, ends gently, and never reverses.
        from winduo2.effect.controller import CLOSING_OUT_DURATION

        controller = EffectController(Settings())
        now = 0.0
        for travel in range(0, 41):
            now += 1 / 60
            controller.observe(
                AngleSample(
                    travel=float(travel), velocity=60.0, confidence=1.0,
                    timestamp=now, at_rest=False, absolute_angle=110.0 - travel,
                ),
                now,
            )
            controller.step(now)
        values = []
        started = None
        while True:
            now += 1 / 60
            controller.observe(
                AngleSample(
                    travel=0.0, velocity=0.0, confidence=1.0,
                    timestamp=now, at_rest=True, absolute_angle=70.0,
                ),
                now,
            )
            frame = controller.step(now)
            if started is None:
                started = now
            values.append(controller._travel.value)
            if frame is None or frame.is_final:
                break
        assert now - started == pytest.approx(CLOSING_OUT_DURATION, abs=2 / 60)
        assert all(b <= a + 1e-9 for a, b in zip(values, values[1:], strict=False))
        steps = [a - b for a, b in zip(values, values[1:], strict=False)]
        middle = max(steps)
        assert steps[0] < middle / 3
        assert steps[-1] < middle / 3
        assert values[-1] == 0.0
