"""Recovering lid travel from the laptop's own sensors.

``sensor``      the hinge angle sensor or lid accelerometer, on its own thread.
``estimator``   lid angle to degrees of travel from neutral.
``lidswitch``   the operating system's own open/shut signal.
"""

from winduo2.angle.estimator import AngleSample, TravelEstimator

__all__ = ["AngleSample", "TravelEstimator"]
