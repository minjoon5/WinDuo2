"""The laptop's own sensors, on a background thread, feeding the estimator.

On Windows the lid is read from a hinge angle sensor, which reports the angle
between lid and base directly, tried in this order:

- Windows' own ``HingeAngleSensor``, on machines whose firmware publishes one.
- The **Intel ISH hinge sensor**, a custom sensor (HID usage 0x020B) that
  Galaxy Book 360 models expose instead. Its firmware works the angle out from
  the accelerometers in both lid and base, so it is the true hinge angle
  whatever the base is resting on.

On Linux the lid is read from the **lid accelerometer**, which every
convertible carries for screen rotation. It reports gravity in the screen's own
axes, and the screen's tilt against gravity is the lid angle whenever the base
is level. When the base is not level, the tilt still changes one for one with
the hinge, and the estimator only ever acts on change.

Accelerometer axes, as Windows defines them: x to the right of the screen, y up
the screen, z out of the glass toward the viewer, in g. An upright screen reads
(0, -1, 0) and a screen lying face up reads (0, 0, -1).
"""

from __future__ import annotations

import contextlib
import math
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from winduo2.angle.estimator import AngleSample, TravelEstimator
from winduo2.config import Settings
from winduo2.log import get_logger

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


class _WindowsCustomHinge:
    """The hinge sensor Intel's Integrated Sensor Hub publishes as a custom sensor.

    The angle is the first custom value of each reading. The rest carry a
    second angle, both accelerometer vectors and a posture mode, none of which
    this needs.
    """

    name = "Intel ISH hinge sensor"

    #: The custom sensor type, from HID usage 0x020B.
    TYPE = uuid.UUID("0000020b-766d-4333-8262-27e82dd158b1")
    #: ``SENSOR_DATA_TYPE_CUSTOM_VALUE1``: the property set and its pid.
    ANGLE_KEY = "{C458F8A7-4AE8-4777-9607-2E9BDD65110A} 161"

    def __init__(self, sensor) -> None:
        self._sensor = sensor
        self._key: str | None = None

    @classmethod
    def open(cls) -> _WindowsCustomHinge | None:
        import asyncio

        _enter_winrt()
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.devices.sensors.custom import CustomSensor

        selector = CustomSensor.get_device_selector(cls.TYPE)
        loop = asyncio.new_event_loop()
        try:
            devices = loop.run_until_complete(
                _wait(DeviceInformation.find_all_async_aqs_filter(selector))
            )
            if devices is None or len(devices) == 0:
                return None
            sensor = loop.run_until_complete(_wait(CustomSensor.from_id_async(devices[0].id)))
        except OSError:
            sensor = None
        finally:
            loop.close()
        if sensor is None:
            return None
        try:
            sensor.report_interval = max(int(sensor.minimum_report_interval), 1000 // POLL_RATE)
        except OSError:
            log.info("hinge sensor kept its own report interval")
        return cls(sensor)

    def read(self) -> tuple[float, float] | None:
        reading = self._sensor.get_current_reading()
        if reading is None:
            return None
        properties = reading.properties
        if self._key is None:
            # Matched without regard to case, as the property store does.
            self._key = next(
                (key for key in properties if str(key).upper() == self.ANGLE_KEY), None
            )
            if self._key is None:
                return None
        value = properties.get(self._key)
        if value is None:
            return None
        angle = _unbox_number(value)
        if angle is None:
            return None
        return angle, 1.0 if 0.0 <= angle <= MAX_LID_ANGLE else 0.0

    def close(self) -> None:
        self._sensor = None


def _unbox_number(value) -> float | None:
    """A boxed WinRT number as a float, whatever numeric type the firmware chose."""
    if isinstance(value, int | float):
        return float(value)
    from winrt import system

    for unbox in (
        system.unbox_double,
        system.unbox_single,
        system.unbox_int32,
        system.unbox_uint32,
        system.unbox_int16,
        system.unbox_uint16,
        system.unbox_int64,
        system.unbox_uint64,
        system.unbox_uint8,
    ):
        try:
            return float(unbox(value))
        except Exception:
            continue
    return None


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
    def open(cls, root: Path = IIO_ROOT, prefix: str = "iio:device") -> _LinuxAccelerometer | None:
        # ``prefix`` is only ever changed by the tests: Windows, where CI runs
        # them, does not allow a colon in a directory name.
        devices = [
            d for d in sorted(root.glob(prefix + "*")) if (d / "in_accel_x_raw").exists()
        ]
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
        backends = (_WindowsHinge, _WindowsCustomHinge)
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
        self._thread = threading.Thread(target=self._run, name="winduo2-sensor", daemon=True)
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
