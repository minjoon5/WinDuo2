"""The Windows calls main makes for its log lines and the sign-in wait.

They only run on Windows, so the CI runner is the one place they are
exercised before a release. A wrong ctypes signature in one of them once
crashed the packaged app on start.
"""

from __future__ import annotations

import re
import sys

import pytest

from winduo2 import main

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows API")


@windows_only
def test_process_start_time_reads():
    assert re.fullmatch(r" in \d+\.\d s", main._since_process_start())


@windows_only
def test_uptime_reads():
    assert re.fullmatch(r", \d+ s after the machine started", main._since_boot())


@windows_only
def test_waiting_for_the_shell_returns():
    # A CI runner may have no taskbar at all; the wait must still give up.
    main._wait_for_the_shell(timeout=0.3)


def test_notes_are_empty_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert main._since_process_start() == ""
    assert main._since_boot() == ""


@windows_only
def test_a_second_copy_is_turned_away(monkeypatch):
    import ctypes
    from ctypes import wintypes

    monkeypatch.setattr(main, "INSTANCE_MUTEX", "Local\\WinDuo2.test-instance")
    first = main._claim_single_instance()
    try:
        assert first
        # Opening the same name again, even from this process, finds it held.
        assert main._claim_single_instance() is None
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(first))


def test_there_is_always_room_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert main._claim_single_instance()
