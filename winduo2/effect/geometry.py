"""Where the picture lands on the glass.

Ported from ``DepthOverlay.swift``'s ``DepthGeometry`` (Apache 2.0, Copyright
2026 Makito).

The picture is a sheet hinged to the bottom edge of the screen, leaning back
from the glass as the lid closes. The eye sits straight out from the middle of
the screen, ``viewing_distance_ratio`` screen heights away, and stays there
relative to the glass.

It used to stay put in the room instead, while the glass turned toward it. That
carried the projected top edge *up* past the top of the screen faster than the
lean brought it down, so the picture only narrowed at the top and never visibly
dropped. Measured from the glass, the top edge comes down as the sheet leans.

Opening uses the same projection inverted (``expand``). Where closing draws the
screen as a trapezoid narrowing toward a lowered top edge, opening draws it as
the reverse: the top edge spreads past both sides and up past the top of the
screen, with the bottom edge held on the hinge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["DepthGeometry", "DEFAULT_NEUTRAL_ANGLE", "Profile", "PROFILE_SAMPLES"]

# Where a lid is assumed to be resting, in degrees. Only the
# perspective placement of the eye depends on this, and it is forgiving: the
# effect itself is driven by travel from neutral, not by absolute angle.
DEFAULT_NEUTRAL_ANGLE = 100.0

#: Lookup entries handed to the shader. 128 keeps the linear interpolation
#: between entries well under a pixel of error on a 4K panel.
PROFILE_SAMPLES = 128


@dataclass
class DepthGeometry:
    """Projects the hinged sheet's four corners onto the screen."""

    # Past 90 degrees the picture turns its face away from the glass, which
    # inverts it. Clamp short of that.
    max_separation_degrees: float = 88.0

    # The projection divides by depth. Never let it reach zero, however far the
    # eye ends up behind the glass.
    min_depth_fraction: float = 0.1

    # How far above the screen, in screen heights, an expanded top edge may be
    # reported. Only ``corners`` needs it; the profile never looks past the
    # top of the screen.
    max_expand: float = 10.0

    def _eye(self, viewing_distance_ratio: float, height: float) -> tuple[float, float]:
        """The eye measured along the glass from the hinge, and away from it."""
        depth = max(height * viewing_distance_ratio, height * self.min_depth_fraction)
        return height / 2, depth

    def corners(
        self,
        start_angle: float,
        current_angle: float,
        viewing_distance_ratio: float,
        recession: float,
        screen_size: tuple[float, float],
        expand: bool = False,
    ) -> list[tuple[float, float]]:
        """Corner positions in screen points, y up, origin bottom-left.

        Returned bottom-left, bottom-right, top-right, top-left.

        ``start_angle`` is the lid angle at which the effect began, so travel is
        measured from there. ``recession`` is degrees the picture turns away
        from the glass per degree the lid closes. ``expand`` inverts the
        projection, for opening.
        """
        width, height = float(screen_size[0]), float(screen_size[1])
        if width <= 0 or height <= 0:
            raise ValueError("screen_size must be positive")

        travel = max(start_angle - current_angle, 0.0)
        separation = math.radians(min(recession * travel, self.max_separation_degrees))
        along, depth = self._eye(viewing_distance_ratio, height)

        half = width / 2
        sin_sep = math.sin(separation)
        cos_sep = math.cos(separation)

        if expand:
            # The screen row the closing projection would carry up to the top
            # edge is where the top edge lands: solving that projection for
            # the row that reaches ``height``. Capped so a lean steep enough
            # to put the top at infinity still returns something drawable.
            denominator = depth * cos_sep - (height - along) * sin_sep
            reach = depth / denominator if denominator > 0 else math.inf
            reach = min(reach, self.max_expand)
            scale = depth / (depth + reach * height * sin_sep)
            top = reach * height
            return [
                (0.0, 0.0),
                (width, 0.0),
                (half + half / scale, top),
                (half - half / scale, top),
            ]

        def project(x: float, y: float) -> tuple[float, float]:
            scale = depth / (depth + y * sin_sep)
            return (
                half + (x - half) * scale,
                along + (y * cos_sep - along) * scale,
            )

        return [
            project(0.0, 0.0),
            project(width, 0.0),
            project(width, height),
            project(0.0, height),
        ]

    def profile(
        self,
        start_angle: float,
        current_angle: float,
        viewing_distance_ratio: float,
        recession: float,
        screen_size: tuple[float, float],
        samples: int = PROFILE_SAMPLES,
        expand: bool = False,
    ) -> Profile:
        """The flat-sheet projection, as a per-row lookup the shader can invert.

        The same sheet ``corners`` describes, hinged at the bottom of the
        screen. It used to be able to lean its upper part
        forward as well, which curved the picture; that read as the screen
        bending and was removed.

        The result is a table rather than a homography so the shader's lookup
        stays the same shape. Entry ``i``
        describes screen row ``y = i / (samples - 1) * end``: which picture row
        lands there, as a fraction of the height, and the horizontal scale at
        that row. Rows above ``end`` continue the last entry at ``slope``
        picture rows per screen row, which is what lets the blur reach into the
        black margin past the top edge.

        ``expand`` swaps the two sides of the projection, for opening: screen
        row ``y`` samples the picture where closing would have drawn picture
        row ``y``, at the reciprocal scale. The table then spans the whole
        screen, and every row of it lands inside the picture.
        """
        width, height = float(screen_size[0]), float(screen_size[1])
        if width <= 0 or height <= 0:
            raise ValueError("screen_size must be positive")
        samples = max(int(samples), 2)

        travel = max(start_angle - current_angle, 0.0)
        separation = math.radians(min(recession * travel, self.max_separation_degrees))
        along, depth = self._eye(viewing_distance_ratio, height)

        # Walk up the sheet by arc length.
        steps = samples * 4
        ds = height / steps
        u = b = 0.0
        arc = [0.0]
        rows = [0.0]
        scales = [1.0]
        for i in range(steps):
            u += math.cos(separation) * ds
            b += math.sin(separation) * ds
            scale = depth / (depth + b)
            arc.append((i + 1) * ds / height)
            # Monotonic by construction for any eye in front of the glass; the
            # max() only guards the table against float noise at tiny angles.
            rows.append(max(along + (u - along) * scale, rows[-1]))
            scales.append(scale)

        if expand:
            return _inverted(arc, rows, scales, width, height, samples, ds)

        end = rows[-1]
        if end <= 0.0:
            raise ValueError("the sheet projects to nothing")

        table: list[tuple[float, float]] = []
        j = 0
        for i in range(samples):
            target = end * i / (samples - 1)
            while j < steps - 1 and rows[j + 1] < target:
                j += 1
            span = rows[j + 1] - rows[j]
            t = 0.0 if span <= 1e-12 else (target - rows[j]) / span
            t = min(max(t, 0.0), 1.0)
            table.append(
                (
                    arc[j] + (arc[j + 1] - arc[j]) * t,
                    scales[j] + (scales[j + 1] - scales[j]) * t,
                )
            )

        last = rows[-1] - rows[-2]
        slope = (arc[-1] - arc[-2]) / last if last > 1e-12 else 0.0
        return Profile(table=table, end=end, slope=slope, width=width, height=height)


def _inverted(arc, rows, scales, width, height, samples, ds) -> Profile:
    """The closing walk read the other way round. ``arc`` is uniform, so screen
    rows index straight into it."""
    steps = len(arc) - 1
    table: list[tuple[float, float]] = []
    for i in range(samples):
        position = i / (samples - 1) * steps
        j = min(int(position), steps - 1)
        t = position - j
        row = rows[j] + (rows[j + 1] - rows[j]) * t
        scale = scales[j] + (scales[j + 1] - scales[j]) * t
        table.append((row / height, 1.0 / max(scale, 1e-6)))
    slope = (rows[-1] - rows[-2]) / height / ds
    return Profile(table=table, end=height, slope=slope, width=width, height=height)


@dataclass
class Profile:
    """Screen row to picture row, for the shader. See ``DepthGeometry.profile``."""

    table: list[tuple[float, float]]
    #: Screen row, in points, where the top edge of the picture lands.
    end: float
    #: Picture rows (as a fraction of the height) per screen point above ``end``.
    slope: float
    width: float
    height: float

    def picture_point(self, x: float, y: float) -> tuple[float, float]:
        """Where screen point ``(x, y)`` samples the picture, in picture points.

        The same interpolation the shader does, so tests can check the table
        without a GPU.
        """
        if y >= self.end:
            row, scale = self.table[-1]
            row += (y - self.end) * self.slope
        else:
            position = max(y, 0.0) / self.end * (len(self.table) - 1)
            i = min(int(position), len(self.table) - 2)
            t = position - i
            (r0, s0), (r1, s1) = self.table[i], self.table[i + 1]
            row, scale = r0 + (r1 - r0) * t, s0 + (s1 - s0) * t
        half = self.width / 2
        return (half + (x - half) / scale, row * self.height)

    def flat(self) -> list[float]:
        """The table as a flat float list, for ``glUniform2fv``."""
        return [value for entry in self.table for value in entry]
