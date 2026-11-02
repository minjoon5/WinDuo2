"""The lid sensor: gravity to angle, the Intel hinge reader, and the reading thread."""

from __future__ import annotations

import math
import time

import pytest

from winduo2.angle import sensor
from winduo2.angle.sensor import SensorAngleSource, lid_angle_from_gravity
from winduo2.config import Settings


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


class _Reading:
    def __init__(self, properties):
        self.properties = properties


class _CustomSensor:
    def __init__(self, properties):
        self.properties = properties

    def get_current_reading(self):
        return None if self.properties is None else _Reading(self.properties)


class TestIntelHinge:
    KEY = "{C458F8A7-4AE8-4777-9607-2E9BDD65110A}"

    def test_reads_the_first_custom_value_as_the_angle(self):
        hinge = sensor._WindowsCustomHinge(
            _CustomSensor({f"{self.KEY} 161": 118, f"{self.KEY} 162": 30})
        )
        assert hinge.read() == (118.0, 1.0)

    def test_matches_the_key_without_regard_to_case(self):
        hinge = sensor._WindowsCustomHinge(_CustomSensor({f"{self.KEY.lower()} 161": 42.5}))
        assert hinge.read() == (42.5, 1.0)

    def test_folded_past_flat_is_not_trusted(self):
        hinge = sensor._WindowsCustomHinge(_CustomSensor({f"{self.KEY} 161": 300}))
        assert hinge.read() == (300.0, 0.0)

    def test_no_reading_or_no_angle_is_none(self):
        assert sensor._WindowsCustomHinge(_CustomSensor(None)).read() is None
        assert sensor._WindowsCustomHinge(_CustomSensor({f"{self.KEY} 162": 30})).read() is None
