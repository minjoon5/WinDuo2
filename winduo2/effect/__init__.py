"""Geometry, easing, and the state machine that drive the depth effect."""

from winduo2.effect.geometry import DEFAULT_NEUTRAL_ANGLE, DepthGeometry
from winduo2.effect.gradient import BlurGradient
from winduo2.effect.homography import screen_to_picture, square_to_quad
from winduo2.effect.spring import CriticallyDampedSpring

__all__ = [
    "DEFAULT_NEUTRAL_ANGLE",
    "BlurGradient",
    "CriticallyDampedSpring",
    "DepthGeometry",
    "screen_to_picture",
    "square_to_quad",
]
