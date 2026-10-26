"""The laptop's own sensors, on a background thread, feeding the estimator.

Two kinds of sensor can report the lid, tried in this order:

- A **hinge angle sensor**, which reports the angle between lid and base
  directly. Windows exposes it as ``HingeAngleSensor`` on machines whose
  firmware publishes one.
- The **lid accelerometer**, which every convertible carries for screen
  rotation. It reports gravity in the screen's own axes, and the screen's tilt
  against gravity is the lid angle whenever the base is level. When the base is
  not level, the tilt still changes one for one with the hinge, and the
  estimator only ever acts on change.

Windows axes: x to the right of the screen, y up the screen, z out of the glass
toward the viewer, in g. An upright screen reads (0, -1, 0) and a screen lying
face up reads (0, 0, -1).
"""

from __future__ import annotations

import contextlib
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from winduo.angle.estimator import AngleSample, TravelEstimator
from winduo.config import Settings
from winduo.log import get_logger

log = get_logger("sensor")

__all__ = [
    "LidSensor",
    "SensorAngleSource",
    "SensorStatus",
    "lid_angle_from_gravity",
    "open_lid_sensor",
]

#: Readings per second. The overlay draws at 60, and most laptop accelerometers
#: report about that fast.
POLL_RATE = 60

#: A lid folded past flat is on its way to tent or tablet. Unfolding it from
#: there reads as closing, and must not start the effect.
MAX_LID_ANGLE = 185.0

#: Standard gravity, for sensors that report in m/s^2.
GRAVITY = 9.80665


@dataclass(frozen=True)
class SensorStatus:
    """Why the estimator is or is not producing anything."""

    running: bool = False
    #: Set when no sensor could be opened or it stopped answering.
    problem: str = ""
    #: Which sensor is in use, for the settings window.
    name: str = ""
    #: Measured readings per second.
    rate: float = 0.0

    @property
    def is_healthy(self) -> bool:
        return self.running and not self.problem


class LidSensor(Protocol):
    #: Shown to the user.
    name: str

    def read(self) -> tuple[float, float] | None:
        """The lid angle in degrees and a confidence from 0 to 1, or ``None``."""

    def close(self) -> None: ...


def lid_angle_from_gravity(x: float, y: float, z: float) -> tuple[float, float]:
    """Lid angle and confidence from one accelerometer reading, Windows axes.

    The hinge runs along x, so only the y-z plane carries the angle: 0 is a lid
    lying shut on a level base, 90 upright, 180 flat open.

    Confidence falls on two things. A laptop standing on its side puts gravity
    along the hinge and leaves almost nothing in the y-z plane to measure. And
    a reading far from 1 g means the laptop is being moved, so part of what the
    sensor sees is the hand rather than gravity.
    """
    magnitude = math.sqrt(x * x + y * y + z * z)
    if magnitude < 1e-6:
        return 0.0, 0.0
    angle = math.degrees(math.atan2(-y, z)) % 360.0
    in_plane = math.hypot(y, z) / magnitude
    level = _ramp(in_plane, 0.35, 0.75)
    still = 1.0 - _ramp(abs(magnitude - 1.0), 0.08, 0.25)
    return angle, level * still


def _ramp(value: float, low: float, high: float) -> float:
    return min(max((value - low) / (high - low), 0.0), 1.0)


# --- Windows ---------------------------------------------------------------


def _enter_winrt() -> None:
    """Put the calling thread in the multithreaded apartment WinRT expects."""
    from winrt import runtime

    # Raises when the thread is already initialised, which is fine either way.
    with contextlib.suppress(OSError):
        runtime.init_apartment(runtime.MTA)


class _WindowsHinge:
    name = "hinge angle sensor"

    def __init__(self, sensor, loop) -> None:
        self._sensor = sensor
        self._loop = loop

    @classmethod
    def open(cls) -> _WindowsHinge | None:
        import asyncio

        _enter_winrt()
        from winrt.windows.devices.sensors import HingeAngleSensor

        loop = asyncio.new_event_loop()
        try:
            sensor = loop.run_until_complete(_wait(HingeAngleSensor.get_default_async()))
        except OSError:
            sensor = None
        if sensor is None:
            loop.close()
            return None
        return cls(sensor, loop)

    def read(self) -> tuple[float, float] | None:
        reading = self._loop.run_until_complete(_wait(self._sensor.get_current_reading_async()))
        if reading is None:
            return None
        angle = float(reading.angle_in_degrees)
        return angle, 1.0 if 0.0 <= angle <= MAX_LID_ANGLE else 0.0

    def close(self) -> None:
        self._loop.close()


async def _wait(operation):
    return await operation


class _WindowsAccelerometer:
    name = "lid accelerometer"

    def __init__(self, sensor) -> None:
        self._sensor = sensor

    @classmethod
    def open(cls) -> _WindowsAccelerometer | None:
        _enter_winrt()
        from winrt.windows.devices.sensors import Accelerometer

        try:
            sensor = Accelerometer.get_default()
        except OSError:
            sensor = None
        if sensor is None:
            return None
        try:
            sensor.report_interval = max(int(sensor.minimum_report_interval), 1000 // POLL_RATE)
        except OSError:
            log.info("accelerometer kept its own report interval")
        return cls(sensor)

    def read(self) -> tuple[float, float] | None:
        reading = self._sensor.get_current_reading()
        if reading is None:
            return None
        angle, confidence = lid_angle_from_gravity(
            reading.acceleration_x, reading.acceleration_y, reading.acceleration_z
        )
        return angle, confidence if angle <= MAX_LID_ANGLE else 0.0

    def close(self) -> None:
        self._sensor = None


# --- Linux -----------------------------------------------------------------

IIO_ROOT = Path("/sys/bus/iio/devices")


class _LinuxAccelerometer:
    """An IIO accelerometer, preferring the one the kernel labels as the display's.

    IIO reports the reaction to gravity, so an upright screen reads +1 g up the
    screen where Windows reads -1 g. Not every driver follows that, so the sign
    is settled from the first confident reading instead: a lid in ordinary use
    is between shut and flat, and only one of the two signs puts it there.
    """

    name = "accelerometer"

    def __init__(self, device: Path) -> None:
        self.name = f"accelerometer ({_read(device / 'name') or device.name})"
        self._device = device
        self._matrix = _mount_matrix(device)
        self._scale = _number(device, "in_accel_scale", 1.0)
        self._offset = _number(device, "in_accel_offset", 0.0)
        self._sign: float | None = None

    @classmethod
    def open(cls, root: Path = IIO_ROOT) -> _LinuxAccelerometer | None:
        devices = [d for d in sorted(root.glob("iio:device*")) if (d / "in_accel_x_raw").exists()]
        if not devices:
            return None

        def rank(device: Path) -> int:
            label = (_read(device / "label") or "").lower()
            if "display" in label or "lid" in label:
                return 0
            return 2 if "base" in label else 1

        return cls(min(devices, key=rank))

    def read(self) -> tuple[float, float] | None:
        raw = []
        for axis in "xyz":
            value = _read(self._device / f"in_accel_{axis}_raw")
            if value is None:
                return None
            raw.append((float(value) + self._offset) * self._scale / GRAVITY)
        x, y, z = (sum(m * v for m, v in zip(row, raw, strict=True)) for row in self._matrix)

        if self._sign is None:
            angle, confidence = lid_angle_from_gravity(-x, -y, -z)
            if confidence < 0.5:
                return angle, 0.0
            self._sign = -1.0 if angle <= MAX_LID_ANGLE else 1.0
        angle, confidence = lid_angle_from_gravity(self._sign * x, self._sign * y, self._sign * z)
        return angle, confidence if angle <= MAX_LID_ANGLE else 0.0

    def close(self) -> None:
        pass


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _number(device: Path, name: str, default: float) -> float:
    try:
        return float(_read(device / name) or default)
    except ValueError:
        return default


def _mount_matrix(device: Path) -> list[list[float]]:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    text = _read(device / "in_accel_mount_matrix") or _read(device / "mount_matrix")
    if not text:
        return identity
    try:
        rows = [[float(v) for v in row.split(",")] for row in text.split(";")]
    except ValueError:
        return identity
    return rows if len(rows) == 3 and all(len(r) == 3 for r in rows) else identity


# --- Choosing --------------------------------------------------------------


def open_lid_sensor() -> LidSensor | None:
    """The best sensor this machine offers, or ``None``. Call on the reading thread."""
    if sys.platform == "win32":
        backends = (_WindowsHinge, _WindowsAccelerometer)
    elif sys.platform.startswith("linux"):
        backends = (_LinuxAccelerometer,)
    else:
        backends = ()
    for backend in backends:
        try:
            sensor = backend.open()
        except ImportError:
            log.warning("the Windows sensor packages are not installed", exc_info=True)
            return None
        except Exception:
            log.exception("could not open %s", backend.name)
            continue
        if sensor is not None:
            log.info("reading the lid from the %s", sensor.name)
            return sensor
        log.info("no %s on this machine", backend.name)
    return None


def _estimator_settings(settings: Settings) -> dict[str, float]:
    return {
        "settle_seconds": settings.neutral_settle_seconds,
        "settle_tolerance": settings.neutral_settle_tolerance,
        "hold_seconds": settings.partial_hold_seconds,
        # Anywhere the effect can still be running, which is the trigger less
        # the hysteresis that releases it.
        "hold_travel": max(settings.trigger_travel - settings.release_hysteresis, 1.0),
    }


class SensorAngleSource:
    """Owns the sensor thread and publishes the newest :class:`AngleSample`.

    The controller polls :meth:`latest` rather than receiving callbacks, which
    keeps every consumer on its own clock.
    """

    def __init__(self, settings: Settings, opener=open_lid_sensor) -> None:
        self._estimator = TravelEstimator(**_estimator_settings(settings))
        self._opener = opener
        self._lock = threading.Lock()
        self._sample: AngleSample | None = None
        self._status = SensorStatus()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- Lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="winduo-sensor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread:
            thread.join(timeout=timeout)
        with self._lock:
            self._status = SensorStatus()
            self._sample = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def retune(self, settings: Settings) -> None:
        self._estimator.retune(**_estimator_settings(settings))

    # --- Reading ---------------------------------------------------------

    def latest(self, max_age: float = 0.4) -> AngleSample | None:
        """The newest sample, or ``None`` when it is too old to act on."""
        with self._lock:
            sample = self._sample
        if sample is None or time.monotonic() - sample.timestamp > max_age:
            return None
        return sample

    def status(self) -> SensorStatus:
        with self._lock:
            return self._status

    # --- Thread ----------------------------------------------------------

    def _run(self) -> None:
        sensor = self._opener()
        if sensor is None:
            with self._lock:
                self._status = SensorStatus(
                    problem="No lid angle sensor or accelerometer was found."
                )
            return

        with self._lock:
            self._status = SensorStatus(running=True, name=sensor.name)

        interval = 1.0 / POLL_RATE
        misses = 0
        window_started = time.monotonic()
        window_count = 0
        rate = 0.0
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    reading = sensor.read()
                except Exception:
                    log.debug("sensor read failed", exc_info=True)
                    reading = None
                now = time.monotonic()

                if reading is None:
                    misses += 1
                    self._publish(self._estimator.note_dropped_reading(now))
                    if misses > POLL_RATE * 2:
                        with self._lock:
                            self._status = SensorStatus(
                                problem="The sensor stopped answering.", name=sensor.name
                            )
                        return
                else:
                    misses = 0
                    window_count += 1
                    if now - window_started >= 1.0:
                        rate = window_count / (now - window_started)
                        window_started, window_count = now, 0
                    self._publish(self._estimator.feed(*reading, now))
                    with self._lock:
                        self._status = SensorStatus(running=True, name=sensor.name, rate=rate)

                self._stop.wait(max(interval - (time.monotonic() - started), 0.001))
        finally:
            sensor.close()
            log.info("sensor closed")

    def _publish(self, sample: AngleSample | None) -> None:
        if sample is None:
            return
        with self._lock:
            self._sample = sample
