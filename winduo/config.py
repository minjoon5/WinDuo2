"""User settings, persisted as JSON in ``config.json`` under ``%APPDATA%\\WinDuo``."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from winduo.log import APP_DIR, get_logger

log = get_logger("config")

__all__ = ["Settings", "Store"]

CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class Settings:
    """Everything the settings window owns.

    Angles are degrees of closing travel away from neutral, not absolute lid
    angles. Neutral is wherever the lid last held still, so nothing here needs
    to know the true hinge angle.
    """

    # --- Master switches -------------------------------------------------
    enabled: bool = True
    #: Keep the picture updating, instead of holding the frame from the trigger.
    live_picture: bool = True

    # --- When the effect starts -----------------------------------------
    #: Degrees of closing away from neutral that start the effect.
    trigger_travel: float = 10.0
    #: Further degrees of closing to reach full strength.
    #:
    #: Worth not shortening. Dimming deliberately leads blur, so compressing the
    #: ramp hits the dimming hardest and the picture reaches black before the
    #: blur has developed enough to see. Windows does cut the display partway
    #: through a close, which is an argument for a shorter ramp, but the answer
    #: to that is to let the effect be unfinished rather than to rush it: macOS
    #: sleeps partway through too.
    #:
    #: 75 rather than the reference implementation's 60, because the controller
    #: now holds the perspective at the end of this ramp instead of letting it
    #: stretch on to a fully shut lid. At 60 the ramp ended at 70 degrees of
    #: travel and the picture stopped visibly short of the reach a real close
    #: used to show; 75 ends at 85, which is about as far as the projection goes
    #: before the eye passes behind the glass and the far edge collapses.
    full_effect_travel: float = 75.0
    #: Degrees of reopening past the trigger before the effect is released.
    #: Stops the effect flickering when the lid hovers on the threshold.
    release_hysteresis: float = 4.0

    # --- Look ------------------------------------------------------------
    #: Gaussian blur radius at the far edge, in points.
    #: 90 rather than 135: with the tilt eased, the picture stays in view to the
    #: end of a close, and at 135 what stays in view is unreadable mush.
    max_blur_radius: float = 90.0
    #: Blur at the hinge edge as a fraction of the blur at the far edge.
    #: 0 blurs the far edge only, 1 blurs the whole picture evenly.
    blur_evenness: float = 0.0
    #: Opacity of the darkening where the blur is at full strength.
    #: Short of black on purpose. A picture that holds still in the room should
    #: still be faintly there at the end of a close, not vanish.
    max_dim: float = 0.85
    #: Height at which the dimming reaches full strength, as a fraction of the
    #: screen height.
    dim_reach: float = 0.5
    #: Strength of a narrow specular line at the hinge edge, 0 to 1. The idea is
    #: adapted from macTilt's hinge highlight (github.com/lqSky7/
    #: iphone-duo-macos-animation, MIT): a real hinge catches a little light
    #: along its crease as the screen turns away from it, and a flat gradient
    #: alone never produces that. 0 turns it off.
    hinge_glow: float = 0.5
    #: Strength of a soft pale band partway up the picture, 0 to 1. Adapted the
    #: same way as ``hinge_glow``, standing in for a hinted reflection off the
    #: turning glass rather than a literal one. 0 turns it off.
    reflection_intensity: float = 0.5

    # --- Perspective -----------------------------------------------------
    #: Eye distance from the middle of the screen, in screen heights.
    #:
    #: 3 is roughly a laptop at arm's length: a 20 cm tall panel seen from about
    #: 60 cm. The perspective has to be about that strong for the picture to
    #: read as holding still in the room; at 6 it reads as a flat squash.
    viewing_distance: float = 3.0
    #: Degrees the picture turns away from the glass per degree of closing.
    #: 1.0 holds the picture still in the room.
    recession: float = 1.0
    #: How far the upper part of the picture leans forward, toward the viewer,
    #: while the part near the hinge stays still in the room. 0 is a flat
    #: sheet; 1 brings the top edge parallel to the glass. A little of it keeps
    #: what sits near the top of the screen readable for longer into a close.
    top_lean: float = 0.6
    #: The most the picture tilts, in degrees past the trigger, however far the
    #: lid goes. The tilt follows the lid one for one at first, so the picture
    #: holds still in the room, then eases toward this. 0 turns the easing off
    #: and tilts all the way to the end of the ramp.
    tilt_limit: float = 55.0

    # --- Neutral detection ----------------------------------------------
    #: How long the lid must hold still before its position becomes neutral.
    neutral_settle_seconds: float = 0.4
    #: How far the lid may drift and still count as holding still, in degrees.
    neutral_settle_tolerance: float = 1.5

    # --- Estimator -------------------------------------------------------
    #: Estimator confidence below which the effect will not trigger.
    confidence_floor: float = 0.35
    #: Capture the screen no wider than this, in pixels. The picture is
    #: receding and blurring while the effect runs, so full resolution buys
    #: very little and costs a lot of memory bandwidth. 0 means native.
    render_width_cap: int = 1600

    # --- Housekeeping ----------------------------------------------------
    launch_at_login: bool = False
    #: Draw the live travel reading in the tray tooltip.
    show_reading: bool = True
    #: The first-run introduction has been shown.
    has_seen_intro: bool = False

    def clamped(self) -> Settings:
        """A copy with every value inside the range the interface offers."""
        out = Settings(**asdict(self))
        out.trigger_travel = _clamp(out.trigger_travel, 2.0, 40.0)
        out.full_effect_travel = _clamp(out.full_effect_travel, 5.0, 90.0)
        out.release_hysteresis = _clamp(out.release_hysteresis, 0.5, 15.0)
        out.max_blur_radius = _clamp(out.max_blur_radius, 10.0, 200.0)
        out.blur_evenness = _clamp(out.blur_evenness, 0.0, 1.0)
        out.max_dim = _clamp(out.max_dim, 0.0, 1.0)
        out.dim_reach = _clamp(out.dim_reach, 0.2, 1.0)
        out.hinge_glow = _clamp(out.hinge_glow, 0.0, 1.0)
        out.reflection_intensity = _clamp(out.reflection_intensity, 0.0, 1.0)
        out.viewing_distance = _clamp(out.viewing_distance, 1.0, 6.0)
        out.recession = _clamp(out.recession, 0.0, 3.0)
        out.top_lean = _clamp(out.top_lean, 0.0, 1.0)
        out.tilt_limit = _clamp(out.tilt_limit, 0.0, 90.0)
        out.neutral_settle_seconds = _clamp(out.neutral_settle_seconds, 0.15, 3.0)
        out.neutral_settle_tolerance = _clamp(out.neutral_settle_tolerance, 0.3, 6.0)
        out.confidence_floor = _clamp(out.confidence_floor, 0.0, 0.95)
        out.render_width_cap = int(_clamp(out.render_width_cap, 0, 7680))
        return out


#: Perspective runs the other way from viewing distance in the interface: a
#: nearer eye converges more sharply. These are the two ends of that slider.
NEAREST_EYE = 1.0
FARTHEST_EYE = 6.0


def perspective_from_distance(distance: float) -> float:
    """Slider position, 0 to 1, from an eye distance in screen heights."""
    return (FARTHEST_EYE - distance) / (FARTHEST_EYE - NEAREST_EYE)


def distance_from_perspective(perspective: float) -> float:
    """Eye distance in screen heights, from a slider position 0 to 1."""
    return FARTHEST_EYE - _clamp(perspective, 0.0, 1.0) * (FARTHEST_EYE - NEAREST_EYE)


class Store:
    """Loads, saves, and broadcasts changes to settings."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self.settings = _load(config_path, Settings).clamped()

    # --- Change notification --------------------------------------------

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener()
            except Exception:
                log.exception("settings listener failed")

    # --- Mutation --------------------------------------------------------

    def update(self, **changes: Any) -> None:
        """Apply named changes, clamp, persist, and notify."""
        known = {f.name for f in fields(Settings)}
        unknown = set(changes) - known
        if unknown:
            raise KeyError(f"unknown settings: {sorted(unknown)}")
        with self._lock:
            for key, value in changes.items():
                setattr(self.settings, key, value)
            self.settings = self.settings.clamped()
            _save(self._config_path, self.settings)
        self._notify()

    def reset_settings(self) -> None:
        """Back to factory values."""
        with self._lock:
            self.settings = Settings()
            _save(self._config_path, self.settings)
        self._notify()


def _clamp(value: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return low
    if value != value:  # NaN
        return low
    return low if value < low else high if value > high else value


def _load(path: Path, kind):
    if not path.exists():
        return kind()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("could not read %s, using defaults", path.name, exc_info=True)
        return kind()
    if not isinstance(raw, dict):
        return kind()
    known = {f.name for f in fields(kind)}
    dropped = set(raw) - known
    if dropped:
        log.info("ignoring retired keys in %s: %s", path.name, sorted(dropped))
    return kind(**{k: v for k, v in raw.items() if k in known})


def _save(path: Path, value) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and swap, so a crash mid-write cannot leave a
        # truncated settings file behind.
        scratch = path.with_suffix(path.suffix + ".tmp")
        scratch.write_text(json.dumps(asdict(value), indent=2), encoding="utf-8")
        scratch.replace(path)
    except OSError:
        log.warning("could not save %s", path.name, exc_info=True)
