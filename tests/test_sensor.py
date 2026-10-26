"""The lid sensor: gravity to angle, the Linux reader, and the reading thread."""

from __future__ import annotations

import math
import time

import pytest

from winduo.angle import sensor
from winduo.angle.sensor import SensorAngleSource, lid_angle_from_gravity
from winduo.config import Settings


def gravity_for(angle: float) -> tuple[float, float, float]:
    """What a Windows lid accelerometer reads on a level base, 1 g."""
    rad = math.radians(angle)
    return 0.0, -math.sin(rad), math.cos(rad)


class TestGravity:
    @pytest.mark.parametrize("angle", [10.0, 45.0, 90.0, 110.0, 135.0, 170.0])
    def test_recovers_the_lid_angle_on_a_level_base(self, angle):
        measured, confidence = lid_angle_from_gravity(*gravity_for(angle))
        assert measured == pytest.approx(angle)
        assert confidence == pytest.approx(1.0)

    def test_upright_and_flat_match_the_windows_axes(self):
        assert lid_angle_from_gravity(0.0, -1.0, 0.0)[0] == pytest.approx(90.0)
        assert lid_angle_from_gravity(0.0, 0.0, -1.0)[0] == pytest.approx(180.0)

    def test_a_laptop_on_its_side_is_not_trusted(self):
        # Gravity along the hinge leaves nothing in the plane the lid turns in.
        assert lid_angle_from_gravity(1.0, -0.05, 0.02)[1] == 0.0

    def test_a_laptop_being_moved_is_not_trusted(self):
        x, y, z = gravity_for(110.0)
        assert lid_angle_from_gravity(x * 1.4, y * 1.4, z * 1.4)[1] == 0.0
        assert lid_angle_from_gravity(x * 1.05, y * 1.05, z * 1.05)[1] == 1.0

    def test_no_reading_is_no_confidence(self):
        assert lid_angle_from_gravity(0.0, 0.0, 0.0) == (0.0, 0.0)


def _iio(root, name, label, x, y, z, scale=1.0, matrix=None):
    device = root / name
    device.mkdir()
    (device / "name").write_text("accel_3d\n")
    if label:
        (device / "label").write_text(label + "\n")
    for axis, value in zip("xyz", (x, y, z), strict=True):
        (device / f"in_accel_{axis}_raw").write_text(f"{value}\n")
    (device / "in_accel_scale").write_text(f"{scale}\n")
    if matrix:
        (device / "in_accel_mount_matrix").write_text(matrix + "\n")
    return device


class TestLinux:
    def test_reads_the_display_accelerometer_not_the_base(self, tmp_path):
        g = sensor.GRAVITY
        # IIO convention: +1 g up the screen when it is upright.
        _iio(tmp_path, "iio:device0", "accel-base", 0, 0, g)
        _iio(tmp_path, "iio:device1", "accel-display", 0, g, 0)
        reader = sensor._LinuxAccelerometer.open(tmp_path)
        angle, confidence = reader.read()
        assert angle == pytest.approx(90.0)
        assert confidence == pytest.approx(1.0)

    def test_settles_the_sign_from_the_first_reading(self, tmp_path):
        # A driver that reports Windows-style, -1 g up an upright screen.
        _, y, z = gravity_for(110.0)
        g = sensor.GRAVITY
        _iio(tmp_path, "iio:device0", None, 0, y * g * 1000, z * g * 1000, scale=0.001)
        angle, _ = sensor._LinuxAccelerometer.open(tmp_path).read()
        assert angle == pytest.approx(110.0)

    def test_applies_the_mount_matrix(self, tmp_path):
        g = sensor.GRAVITY
        # Mounted rotated a quarter turn: the screen's up is the chip's x.
        _iio(tmp_path, "iio:device0", None, g, 0, 0, matrix="0, 1, 0; 1, 0, 0; 0, 0, 1")
        angle, _ = sensor._LinuxAccelerometer.open(tmp_path).read()
        assert angle == pytest.approx(90.0)

    def test_nothing_there_is_none(self, tmp_path):
        assert sensor._LinuxAccelerometer.open(tmp_path) is None


class _ScriptedSensor:
    name = "scripted sensor"

    def __init__(self, angle=110.0):
        self.angle = angle
        self.closed = False

    def read(self):
        return self.angle, 1.0

    def close(self):
        self.closed = True


def _wait_for(predicate, timeout=2.0):
    ends = time.monotonic() + timeout
    while time.monotonic() < ends:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestSource:
    def test_publishes_samples_and_closes_the_sensor(self):
        scripted = _ScriptedSensor()
        source = SensorAngleSource(Settings(), opener=lambda: scripted)
        source.start()
        try:
            assert _wait_for(lambda: source.latest() is not None)
            assert source.status().name == "scripted sensor"
            assert source.latest().absolute_angle == pytest.approx(110.0)
        finally:
            source.stop()
        assert scripted.closed
        assert source.latest() is None

    def test_no_sensor_is_reported_as_a_problem(self):
        source = SensorAngleSource(Settings(), opener=lambda: None)
        source.start()
        try:
            assert _wait_for(lambda: bool(source.status().problem))
            assert "No lid angle sensor" in source.status().problem
        finally:
            source.stop()
