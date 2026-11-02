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
