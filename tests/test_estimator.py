"""Behaviour of the travel estimator, all driven through the shared clock."""

from __future__ import annotations

import pytest
from helpers import Driver

from winduo.angle.estimator import TravelEstimator


class TestNeutral:
    def test_a_still_lid_reads_no_travel(self):
        sample = Driver().settle()
        assert sample.travel == pytest.approx(0.0, abs=1e-9)
        assert sample.at_rest

    def test_rest_needs_the_settle_time_to_pass(self):
        driver = Driver(settle_seconds=0.4)
        early = driver.feed([0.0] * 6)
        assert not early.at_rest
        late = driver.feed([0.0] * 30)
        assert late.at_rest

    def test_a_new_resting_position_becomes_the_new_zero(self):
        # A lid that stops partway becomes neutral, so travel collapses and the
        # effect eases out instead of sitting on screen.
        driver = Driver(settle_seconds=0.4)
        driver.settle()
        moved = driver.feed([1.0] * 20)
        assert moved.travel == pytest.approx(20.0, rel=0.02)

        rested = driver.feed([0.0] * 60)
        assert rested.at_rest
        assert rested.travel == pytest.approx(0.0, abs=1e-6)

    def test_a_slow_close_is_not_swallowed_by_the_neutral_tracker(self):
        # 0.25 degrees per reading at 60 Hz is 15 degrees per second: about as
        # slowly as anyone actually closes a lid.
        driver = Driver(settle_seconds=0.4, settle_tolerance=1.5)
        driver.settle()
        sample = driver.feed([0.25] * 60)
        assert sample.travel == pytest.approx(15.0, rel=0.02)
        assert not sample.at_rest

    def test_a_sustained_drift_never_re_zeros_however_long_it_runs(self):
        # Tolerance 1.5 degrees over a 0.4 s window rejects anything above
        # 3.75 degrees per second; this is 6.
        driver = Driver(settle_seconds=0.4, settle_tolerance=1.5)
        driver.settle()
        sample = driver.feed([0.1] * 600)
        assert sample.travel == pytest.approx(60.0, rel=0.02)
        assert not sample.at_rest

    def test_motion_slower_than_the_rest_threshold_counts_as_rest(self):
        # A settling hinge is not a lid close.
        driver = Driver(settle_seconds=0.4, settle_tolerance=1.5)
        driver.settle()
        sample = driver.feed([0.02] * 400)
        assert sample.at_rest
        assert abs(sample.travel) < 1.5

    def test_noise_inside_the_tolerance_does_not_break_rest(self):
        driver = Driver(settle_seconds=0.4, settle_tolerance=1.5)
        driver.settle()
        sample = driver.feed([0.4, -0.4] * 40)
        assert sample.at_rest


class TestTravel:
    def test_closing_gives_positive_travel(self):
        driver = Driver(settle_tolerance=0.2)
        driver.settle()
        sample = driver.feed([0.5] * 12)
        assert sample.travel == pytest.approx(6.0)
        assert sample.is_closing

    def test_opening_past_neutral_gives_negative_travel(self):
        driver = Driver()
        driver.settle()
        sample = driver.feed([-0.5] * 10)
        assert sample.travel < 0
        assert not sample.is_closing

    def test_velocity_matches_the_rate_it_was_fed(self):
        driver = Driver()
        driver.settle()
        # 2 degrees per reading at 60 Hz is 120 degrees per second.
        sample = driver.feed([2.0] * 12)
        assert sample.velocity == pytest.approx(120.0, rel=0.02)

    def test_coming_to_rest_after_opening_is_not_a_close(self):
        # Measured on a Galaxy Book: opening 45 degrees past neutral and
        # stopping re-zeroed travel from -45 to 0 in one reading, which a fit
        # over travel reported as a 303 degree per second close.
        driver = Driver(settle_seconds=0.4)
        driver.settle()
        driver.feed([-0.5] * 90)
        speeds = []
        for _ in range(60):
            speeds.append(driver.feed([0.0]).velocity)
        assert driver.sample.at_rest
        assert driver.sample.travel == pytest.approx(0.0, abs=1e-6)
        assert max(speeds) < 1.0

    def test_a_still_lid_has_no_velocity(self):
        assert Driver().settle().velocity == pytest.approx(0.0, abs=1e-9)

    def test_the_sensor_angle_is_reported(self):
        driver = Driver(angle=110.0)
        driver.settle()
        sample = driver.feed([5.0])
        assert sample.absolute_angle == pytest.approx(105.0)

    def test_crossing_the_seam_at_zero_is_continuous(self):
        # An accelerometer reading a lid tipped just past vertical-flat on a
        # tilted base can wrap from 359 to 1. That is two degrees, not 358.
        estimator = TravelEstimator()
        estimator.feed(1.0, 1.0, 0.0)
        sample = estimator.feed(359.0, 1.0, 1 / 60)
        assert sample.travel == pytest.approx(2.0)
        assert sample.absolute_angle == pytest.approx(359.0)


class TestConfidence:
    def test_builds_up_over_several_good_readings(self):
        driver = Driver()
        first = driver.feed([0.0])
        later = driver.feed([0.0] * 30)
        assert later.confidence > first.confidence
        assert later.confidence == pytest.approx(1.0)

    def test_collapses_faster_than_it_recovers(self):
        # One bad patch should stop the effect; one good reading should not
        # start it. The asymmetry is deliberate.
        driver = Driver()
        driver.settle()
        before = driver.estimator._confidence
        bad = driver.feed([0.0] * 3, confidence=0.0)
        dropped = before - bad.confidence
        good = driver.feed([0.0] * 3, confidence=1.0)
        assert dropped > (good.confidence - bad.confidence)

    def test_low_confidence_blocks_a_new_neutral(self):
        # A laptop being carried must not redefine neutral.
        driver = Driver(settle_seconds=0.2)
        driver.settle()
        sample = driver.feed([0.0] * 60, confidence=0.05)
        assert not sample.at_rest

    def test_dropped_readings_bleed_confidence_away(self):
        driver = Driver()
        assert driver.settle().confidence > 0.5
        sample = driver.drop(7)
        assert sample.confidence == 0.0
        assert sample.velocity == 0.0

    def test_a_dropped_reading_before_any_reading_reports_nothing(self):
        assert TravelEstimator().note_dropped_reading(0.0) is None


class TestLifecycle:
    def test_reset_forgets_the_neutral_reference(self):
        driver = Driver()
        driver.settle()
        assert driver.feed([5.0] * 3).travel > 0
        driver.estimator.reset()
        fresh = driver.feed([0.0] * 3)
        assert fresh.travel == pytest.approx(0.0, abs=1e-9)

    def test_retune_keeps_the_reference(self):
        driver = Driver(settle_tolerance=0.2)
        driver.settle()
        driver.feed([1.0] * 3)
        driver.estimator.retune(settle_seconds=1.2)
        assert driver.estimator.settle_seconds == 1.2
        assert driver.feed([0.0]).travel == pytest.approx(3.0)
