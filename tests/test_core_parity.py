"""The C++ core (native/core) against the Python implementation it was ported from.

Both are driven with the same inputs, step by step, and must agree: the same
settings, the same projection, the same estimates from the same readings, and
the same decisions and frames from the same estimates and lid events. The
Python side is well covered by the rest of the suite, so agreement here
carries that coverage over to the C++.

The core is built as a shared library: with g++ or clang++ where there is one
(Linux, or a developer machine), or taken prebuilt from native/build by CI on
Windows (native/build.ps1 builds it).
"""

from __future__ import annotations

import ctypes
import json
import math
import random
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from winduo2.angle.estimator import AngleSample, TravelEstimator
from winduo2.config import Settings
from winduo2.effect.controller import EffectController, Phase, tilt_knee
from winduo2.effect.geometry import PROFILE_SAMPLES, DepthGeometry
from winduo2.effect.gradient import BlurGradient

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "native" / "core"
PREBUILT = [
    ROOT / "native" / "build" / "Release" / "winduo2_core_capi.dll",
    ROOT / "native" / "build" / "winduo2_core_capi.dll",
]


class _Sample(ctypes.Structure):
    _fields_ = [
        ("travel", ctypes.c_double),
        ("velocity", ctypes.c_double),
        ("confidence", ctypes.c_double),
        ("timestamp", ctypes.c_double),
        ("at_rest", ctypes.c_int),
        ("absolute_angle", ctypes.c_double),
    ]


class _Frame(ctypes.Structure):
    _fields_ = [
        ("start_angle", ctypes.c_double),
        ("current_angle", ctypes.c_double),
        ("progress", ctypes.c_double),
        ("velocity", ctypes.c_double),
        ("is_final", ctypes.c_int),
        ("opening", ctypes.c_int),
    ]


def _build(tmp: Path) -> Path | None:
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None or sys.platform == "win32":
        return None
    out = tmp / "libwinduo2_core.so"
    subprocess.run(
        [compiler, "-std=c++17", "-O2", "-fPIC", "-shared", "-o", str(out),
         str(CORE / "core.cpp"), str(CORE / "core_capi.cpp")],
        check=True, capture_output=True,
    )
    return out


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    path = next((p for p in PREBUILT if p.exists()), None)
    if path is None:
        try:
            path = _build(tmp_path_factory.mktemp("core"))
        except subprocess.CalledProcessError as error:
            pytest.fail(f"the core did not build:\n{error.stderr.decode(errors='replace')}")
    if path is None:
        pytest.skip("no C++ compiler and no prebuilt core")
    lib = ctypes.CDLL(str(path))
    d, i, p = ctypes.c_double, ctypes.c_int, ctypes.c_void_p
    lib.wdc_settings_roundtrip.argtypes = [ctypes.c_char_p, ctypes.c_char_p, i]
    lib.wdc_estimator_new.argtypes = [d, d, d, d]
    lib.wdc_estimator_new.restype = p
    lib.wdc_estimator_free.argtypes = [p]
    lib.wdc_estimator_feed.argtypes = [p, d, d, d, ctypes.POINTER(_Sample)]
    lib.wdc_estimator_dropped.argtypes = [p, d, ctypes.POINTER(_Sample)]
    lib.wdc_profile.argtypes = [d, d, d, d, d, d, i, i, ctypes.POINTER(d),
                                ctypes.POINTER(d), ctypes.POINTER(d)]
    lib.wdc_corners.argtypes = [d, d, d, d, d, d, i, ctypes.POINTER(d)]
    lib.wdc_gradient.argtypes = [d, d, ctypes.POINTER(d)]
    lib.wdc_tilt_knee.argtypes = [d, d]
    lib.wdc_tilt_knee.restype = d
    lib.wdc_controller_new.argtypes = [ctypes.c_char_p]
    lib.wdc_controller_new.restype = p
    for name in ("free", "lid_shut", "lid_opened", "suspend", "resume"):
        getattr(lib, f"wdc_controller_{name}").argtypes = [p]
    lib.wdc_controller_observe.argtypes = [p, ctypes.POINTER(_Sample), d]
    lib.wdc_controller_step.argtypes = [p, d, ctypes.POINTER(_Frame)]
    lib.wdc_controller_phase.argtypes = [p]
    lib.wdc_controller_poll_interval.argtypes = [p]
    lib.wdc_controller_poll_interval.restype = d
    lib.wdc_controller_travel.argtypes = [p]
    lib.wdc_controller_travel.restype = d
    return lib


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# --- Settings ------------------------------------------------------------------


def _python_json(settings: Settings) -> dict:
    defaults = asdict(Settings())
    return {k: v for k, v in asdict(settings).items() if defaults.get(k) != v}


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"trigger_travel": 12.5, "enabled": False, "render_width_cap": 1600},
        {"max_dim": 7.0, "viewing_distance": -3, "partial_hold_seconds": 100},
        {"retired_key": [1, {"x": 2}], "hinge_glow": 0.25, "name": "x"},
        {"launch_at_login": False, "has_seen_intro": True, "tilt_limit": 0},
    ],
)
def test_settings_read_clamp_and_write_the_same(core, raw):
    expected = _python_json(Settings(**{k: v for k, v in raw.items()
                                        if k in asdict(Settings())}).clamped())
    out = ctypes.create_string_buffer(4096)
    assert core.wdc_settings_roundtrip(json.dumps(raw).encode(), out, 4096) == 1
    assert json.loads(out.value.decode()) == expected


@pytest.mark.parametrize("extreme", [-1e9, 1e9])
def test_every_setting_clamps_the_same(core, extreme):
    numeric = {k: extreme for k, v in asdict(Settings()).items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    expected = _python_json(Settings(**numeric).clamped())
    out = ctypes.create_string_buffer(4096)
    assert core.wdc_settings_roundtrip(json.dumps(numeric).encode(), out, 4096) == 1
    assert json.loads(out.value.decode()) == expected


def test_unreadable_settings_fall_back_to_defaults(core):
    out = ctypes.create_string_buffer(4096)
    assert core.wdc_settings_roundtrip(b"{not json", out, 4096) == 0
    assert json.loads(out.value.decode()) == {}


# --- Geometry and gradients --------------------------------------------------------


def test_the_projection_matches(core):
    rng = random.Random(7)
    geometry = DepthGeometry()
    table = (ctypes.c_double * (2 * PROFILE_SAMPLES))()
    end, slope = ctypes.c_double(), ctypes.c_double()
    corners = (ctypes.c_double * 8)()
    for _ in range(300):
        start = rng.uniform(40, 140)
        current = start - rng.uniform(-10, 120)
        distance = rng.uniform(1, 6)
        recession = rng.uniform(0, 3)
        size = (rng.choice([1920.0, 2880.0, 1280.0]), rng.choice([1080.0, 1800.0, 800.0]))
        expand = rng.random() < 0.4
        ok = core.wdc_profile(start, current, distance, recession, *size, PROFILE_SAMPLES,
                              int(expand), table, ctypes.byref(end), ctypes.byref(slope))
        try:
            profile = geometry.profile(start, current, distance, recession, size, expand=expand)
        except ValueError:
            assert not ok
            continue
        assert ok
        assert close(end.value, profile.end) and close(slope.value, profile.slope)
        for k, (row, scale) in enumerate(profile.table):
            assert close(table[2 * k], row) and close(table[2 * k + 1], scale)
        core.wdc_corners(start, current, distance, recession, *size, int(expand), corners)
        for k, (x, y) in enumerate(
            geometry.corners(start, current, distance, recession, size, expand=expand)
        ):
            assert close(corners[2 * k], x) and close(corners[2 * k + 1], y)


def test_the_gradients_match(core):
    gradient = BlurGradient()
    out = (ctypes.c_double * 4)()
    for progress in [-0.5, 0.0, 0.1, 0.33, 0.5, 0.9, 1.0, 1.7]:
        for velocity in [-400.0, -100.0, 0.0, 29.0, 31.0, 150.0, 300.0]:
            core.wdc_gradient(progress, velocity, out)
            expected = (
                gradient.blur_strength(progress), gradient.dim_strength(progress),
                gradient.turn_strength(progress), gradient.motion_boost(velocity),
            )
            assert all(close(a, b) for a, b in zip(out, expected, strict=True))
    for travel in [-3.0, 0.0, 10.0, 54.0, 200.0]:
        for limit in [0.0, 30.0, 55.0, 90.0]:
            assert close(core.wdc_tilt_knee(travel, limit), tilt_knee(travel, limit))


# --- The estimator -------------------------------------------------------------------


def _lid_readings(seed: int, seconds: float = 30.0, rate: float = 60.0):
    """A lid that rests, closes, pauses, opens, slams and is carried, with
    noise, whole-degree steps, confidence dips and dropped readings."""
    rng = random.Random(seed)
    angle, target, speed = 110.0, 110.0, 0.0
    whole_degrees = rng.random() < 0.5
    t = 1000.0
    readings = []
    while t < 1000.0 + seconds:
        if rng.random() < 0.02:
            target = rng.choice([110.0, 95.0, 60.0, 15.0, 3.0, 125.0, rng.uniform(0, 140)])
            # Slow folds too, which stretch the estimator's rest window to its cap.
            speed = rng.choice([1.0, 3.0, 10.0, 30.0, 90.0, 250.0, 400.0])
        step = speed / rate
        angle += max(-step, min(step, target - angle))
        reading = angle + rng.gauss(0, 0.3)
        if whole_degrees:
            reading = float(round(reading))
        confidence = 1.0 if rng.random() > 0.03 else rng.choice([0.0, 0.3, 0.6])
        dropped = rng.random() < 0.02
        readings.append((t, None if dropped else reading % 360.0, confidence))
        t += 1.0 / rate * rng.uniform(0.8, 1.2)
    return readings


def _sample_matches(c: _Sample, p: AngleSample) -> bool:
    return (
        close(c.travel, p.travel, 1e-7) and close(c.velocity, p.velocity, 1e-6)
        and close(c.confidence, p.confidence) and close(c.timestamp, p.timestamp)
        and bool(c.at_rest) == p.at_rest and close(c.absolute_angle, p.absolute_angle, 1e-7)
    )


@pytest.mark.parametrize("seed", range(12))
def test_the_estimator_matches_reading_for_reading(core, seed):
    settings = (0.4, 1.5, 0.4, 6.0) if seed % 2 else (0.25, 0.8, 1.5, 1.0)
    python = TravelEstimator(*settings)
    native = core.wdc_estimator_new(*settings)
    out = _Sample()
    try:
        for t, angle, confidence in _lid_readings(seed):
            if angle is None:
                expected = python.note_dropped_reading(t)
                got = core.wdc_estimator_dropped(native, t, ctypes.byref(out))
                assert bool(got) == (expected is not None)
                if expected is None:
                    continue
            else:
                expected = python.feed(angle, confidence, t)
                core.wdc_estimator_feed(native, angle, confidence, t, ctypes.byref(out))
            assert _sample_matches(out, expected), (t, out.travel, expected)
    finally:
        core.wdc_estimator_free(native)


def _slow_fold(rate_deg: float, fold: float, whole: bool):
    """Rest, fold ``fold`` degrees at ``rate_deg`` a second, then hold."""
    readings, t, angle = [], 0.0, 110.0
    while t < 2.0:
        readings.append((t, angle, 1.0))
        t += 1 / 60
    while angle > 110.0 - fold:
        angle = max(angle - rate_deg / 60, 110.0 - fold)
        readings.append((t, float(round(angle)) if whole else angle, 1.0))
        t += 1 / 60
    for _ in range(60 * 8):
        readings.append((t, float(round(angle)) if whole else angle, 1.0))
        t += 1 / 60
    return readings


@pytest.mark.parametrize("rate_deg", [0.4, 0.8, 1.5, 4.0])
@pytest.mark.parametrize("whole", [False, True])
def test_the_estimator_matches_on_slow_folds_that_stop(core, rate_deg, whole):
    # Slow enough that the rest window stretches to its cap, then stopping:
    # when neutral re-zeros depends on exactly how long that window is.
    python = TravelEstimator()
    native = core.wdc_estimator_new(0.4, 1.5, 0.4, 6.0)
    out = _Sample()
    try:
        for t, angle, confidence in _slow_fold(rate_deg, 8.0, whole):
            expected = python.feed(angle, confidence, t)
            core.wdc_estimator_feed(native, angle, confidence, t, ctypes.byref(out))
            assert _sample_matches(out, expected), (t, out.travel, expected)
    finally:
        core.wdc_estimator_free(native)


# --- The controller ------------------------------------------------------------------


_PHASES = {0: Phase.IDLE, 1: Phase.RUNNING, 2: Phase.CLOSING_OUT}


def _to_c(sample: AngleSample) -> _Sample:
    return _Sample(sample.travel, sample.velocity, sample.confidence, sample.timestamp,
                   int(sample.at_rest), sample.absolute_angle)


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("tuned", [False, True])
def test_the_controller_decides_and_draws_the_same(core, seed, tuned):
    settings = Settings()
    if tuned:
        settings = Settings(trigger_travel=9.0, full_effect_travel=50.0, tilt_limit=30.0,
                            release_hysteresis=2.0, confidence_floor=0.6).clamped()
    python = EffectController(settings)
    native = core.wdc_controller_new(json.dumps(asdict(settings)).encode())
    estimator = TravelEstimator()
    rng = random.Random(seed + 100)
    frame = _Frame()
    try:
        for t, angle, confidence in _lid_readings(seed, seconds=40.0):
            if angle is None:
                sample = estimator.note_dropped_reading(t)
            else:
                sample = estimator.feed(angle, confidence, t)
            python.observe(sample, t)
            core.wdc_controller_observe(
                native, ctypes.byref(_to_c(sample)) if sample else None, t
            )
            # The lid switch, as Windows reports it at the bottom of a close.
            if angle is not None and angle < 4.0 and rng.random() < 0.3:
                python.lid_shut()
                core.wdc_controller_lid_shut(native)
            elif angle is not None and angle > 30.0 and rng.random() < 0.05:
                python.lid_opened()
                core.wdc_controller_lid_opened(native)
            # The engine steps more often than the sensor reports, sometimes.
            for now in (t, t + 0.008) if rng.random() < 0.3 else (t,):
                expected = python.step(now)
                drew = core.wdc_controller_step(native, now, ctypes.byref(frame))
                assert bool(drew) == (expected is not None), now
                assert _PHASES[core.wdc_controller_phase(native)] is python.phase, now
                if expected is None:
                    continue
                assert close(frame.start_angle, expected.start_angle, 1e-7)
                assert close(frame.current_angle, expected.current_angle, 1e-7), now
                assert close(frame.progress, expected.progress, 1e-7)
                assert bool(frame.is_final) == expected.is_final
                assert bool(frame.opening) == expected.opening
            assert close(core.wdc_controller_poll_interval(native), python.poll_interval)
        assert python.travel == pytest.approx(core.wdc_controller_travel(native), abs=1e-6)
    finally:
        core.wdc_controller_free(native)


def test_the_scenarios_exercise_every_phase(core):
    """Guards the controller test against comparing nothing but idle frames."""
    python = EffectController(Settings())
    estimator = TravelEstimator()
    seen = set()
    for seed in range(12):
        for t, angle, confidence in _lid_readings(seed, seconds=40.0):
            sample = (estimator.note_dropped_reading(t) if angle is None
                      else estimator.feed(angle, confidence, t))
            python.observe(sample, t)
            frame = python.step(t)
            seen.add(python.phase)
            if frame is not None and frame.opening:
                seen.add("opening")
    assert {Phase.IDLE, Phase.RUNNING, Phase.CLOSING_OUT, "opening"} <= seen
