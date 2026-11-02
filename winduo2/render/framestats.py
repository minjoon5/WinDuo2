"""How each run of the effect actually performed, summarised in one log line.

The effect stuttered on real hardware and there was nothing to go on but how
it looked. This keeps a few numbers per frame for the length of one run and
writes them out when the run ends:

- **interval**: time between frames. At the panel's rate every interval is one
  period; a frame that missed its refresh shows as an interval of about two.
- **work**: time the engine spent on a frame on the Qt thread before handing it
  over, with **upload** the part spent moving a newly captured picture to the
  GPU and rebuilding its blur levels.
- **paint**: time in ``paintGL``, the draw itself as the CPU sees it.
- **present**: from handing the frame over to Qt reporting it shown, which
  includes composing and, for a translucent window, reading it back for DWM.

Pure Python and clock-agnostic, so the arithmetic is tested off Windows.
"""

from __future__ import annotations

__all__ = ["FrameStats"]


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


class FrameStats:
    #: An interval this many periods long or more counts as a missed frame.
    MISSED = 1.5

    def __init__(self) -> None:
        self.period = 1 / 60
        self._reset()

    def _reset(self) -> None:
        self.intervals: list[float] = []
        self.work: list[float] = []
        self.uploads: list[float] = []
        self.paints: list[float] = []
        self.presents: list[float] = []
        self._last_frame: float | None = None
        self._first_frame: float | None = None
        self._submitted_at: float | None = None
        self.active = False

    def begin(self, period: float) -> None:
        """A run starts. ``period`` is the frame period the pacer aims for."""
        self._reset()
        self.period = period
        self.active = True

    def frame(self, now: float, work: float, upload: float | None) -> None:
        """One frame handed to Qt at ``now``, after ``work`` seconds of engine
        time, ``upload`` of them spent on a new picture (None if none came)."""
        if not self.active:
            return
        if self._last_frame is not None:
            self.intervals.append(now - self._last_frame)
        else:
            self._first_frame = now
        self._last_frame = now
        self._submitted_at = now
        self.work.append(work)
        if upload is not None:
            self.uploads.append(upload)

    def painted(self, seconds: float) -> None:
        if self.active:
            self.paints.append(seconds)

    def present_took(self, seconds: float) -> None:
        """A renderer that presents synchronously says how long it took."""
        if self.active:
            self.presents.append(seconds)

    def presented(self, now: float) -> None:
        """Qt reports the last handed-over frame shown."""
        if self.active and self._submitted_at is not None:
            self.presents.append(now - self._submitted_at)
            self._submitted_at = None

    @property
    def missed(self) -> int:
        limit = self.period * self.MISSED
        return sum(1 for interval in self.intervals if interval >= limit)

    def summary(self) -> str:
        """The run in one line, for the log. Ends the run."""
        self.active = False
        frames = len(self.work)
        if frames < 2 or self._first_frame is None or self._last_frame is None:
            return f"{frames} frame(s)"
        span = self._last_frame - self._first_frame
        fps = (frames - 1) / span if span > 0 else 0.0

        def ms(values: list[float]) -> str:
            if not values:
                return "-"
            return (
                f"{_percentile(values, 0.5) * 1000:.1f}/"
                f"{_percentile(values, 0.95) * 1000:.1f}/"
                f"{max(values) * 1000:.1f}"
            )

        return (
            f"{frames} frames in {span:.2f} s, {fps:.0f} fps of "
            f"{1 / self.period:.0f}, {self.missed} missed; ms median/p95/max: "
            f"interval {ms(self.intervals)}, work {ms(self.work)}, "
            f"upload {ms(self.uploads)} x{len(self.uploads)}, "
            f"paint {ms(self.paints)}, present {ms(self.presents)}"
        )
