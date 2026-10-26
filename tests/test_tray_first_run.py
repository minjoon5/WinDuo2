"""First run has to show its own controls.

The app has no main window, so on a fresh install the only evidence it is
configurable is a tray icon and a balloon, and both are easy to miss entirely.
These pin the behaviour that fixes that: the settings window opens once, on a
fresh install only.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from winduo.config import Store  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv[:1])


class _FakeSignal:
    def connect(self, *_args, **_kwargs):
        return None


class _FakeSensor:
    @staticmethod
    def latest(max_age: float = 1.0):
        return None


class _FakeEngine:
    """Only what TrayController touches, so no sensor or GL is involved."""

    changed = _FakeSignal()
    problem = ""
    is_effect_running = False
    sensor = _FakeSensor()

    @staticmethod
    def run_preview():
        return None


def _tray(store):
    from winduo.ui.tray import TrayController

    tray = TrayController(store, _FakeEngine())
    opened = []
    tray.open_settings = lambda: opened.append(True)
    return tray, opened


def _show(tray, app):
    from PyQt6.QtTest import QTest

    tray.show()
    # The settings window is opened on a timer, after the balloon.
    QTest.qWait(900)
    tray.icon.hide()


class TestFirstRun:
    def test_a_fresh_install_opens_the_settings_once(self, app, tmp_path):
        store = Store(config_path=tmp_path / "config.json")
        assert not store.settings.has_seen_intro
        tray, opened = _tray(store)
        _show(tray, app)
        assert opened == [True]
        assert store.settings.has_seen_intro

    def test_a_later_launch_does_not(self, app, tmp_path):
        store = Store(config_path=tmp_path / "config.json")
        store.update(has_seen_intro=True)
        tray, opened = _tray(store)
        _show(tray, app)
        assert opened == []
