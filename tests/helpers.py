"""Shared test fixtures: an estimator driven by a simulated lid."""

from __future__ import annotations

from winduo2.angle.estimator import TravelEstimator

#: One sensor reading, at the 60 Hz the sensor thread polls.
READING = 1 / 60


class Driver:
    """An estimator, a lid angle, and a monotonic clock.

    The clock has to be shared across every step of a test. Feeding readings
    with timestamps that jump backwards is not something the estimator is meant
    to survive, and a helper that quietly did it would test nothing.
    """

    def __init__(self, angle: float = 110.0, **kwargs) -> None:
        self.estimator = TravelEstimator(**kwargs)
        self.angle = angle
        self.now = 0.0
        self.sample = None

    def feed(self, steps, confidence: float = 1.0):
        """Close the lid by each step in turn, one reading apart. Negative opens."""
        for step in steps:
            self.now += READING
            self.angle -= step
            self.sample = self.estimator.feed(self.angle, confidence, self.now)
        return self.sample

    def settle(self, seconds: float = 1.0, confidence: float = 1.0):
        """Hold perfectly still long enough to establish neutral."""
        return self.feed([0.0] * int(seconds / READING), confidence=confidence)

    def drop(self, count: int = 1):
        for _ in range(count):
            self.now += READING
            self.sample = self.estimator.note_dropped_reading(self.now)
        return self.sample
