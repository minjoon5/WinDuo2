"""Starting with Windows, through the per-user ``Run`` key in the registry.

The entry is the same one Task Manager lists under *Startup apps*, so it can be
switched off there too. It needs no administrator rights and is removed again
when the setting is turned off. On Linux the install script starts WinDuo2 from
the Hyprland config instead, so everything here is a no-op there.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from winduo2.log import get_logger

log = get_logger("autostart")

__all__ = ["command", "is_supported", "sync"]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "WinDuo2"


def is_supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """What Windows should run at sign-in to start this copy of WinDuo2."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        # The packaged WinDuo2.exe. Written fresh on every start, so moving the
        # file and running it once from its new place keeps the entry working.
        return f'"{executable}"'
    # From source: pythonw rather than python, so no console window opens.
    windowed = executable.with_name("pythonw.exe")
    if windowed.exists():
        executable = windowed
    return f'"{executable}" -m winduo2'


def sync(enabled: bool) -> None:
    """Add or remove the startup entry to match ``enabled``."""
    if not is_supported():
        return
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, VALUE_NAME)
    except OSError:
        log.warning("could not update the startup entry", exc_info=True)
