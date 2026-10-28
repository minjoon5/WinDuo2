"""Starting with Windows, as a Task Scheduler task that runs at sign-in.

It used to be an entry under the per-user ``Run`` key in the registry, the one
Task Manager lists under *Startup apps*. Explorer holds those back after
sign-in and starts them at low priority, so WinDuo2 could take a minute to
appear, and a lid closed in that minute did nothing. A logon task starts as
soon as the user is signed in.

The task is the current user's own, runs without elevation, and is registered
from XML because ``schtasks`` offers no switches for the settings that matter
here: Task Scheduler's defaults would stop the app after three days, refuse to
start it on battery, and run it below normal priority. Should Windows refuse to
register it, the ``Run`` entry is written instead, so the setting still works,
only more slowly. Turning the setting off removes both. On Linux the install
script starts WinDuo2 from the Hyprland config instead, so everything here is a
no-op there.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from winduo2.log import get_logger

log = get_logger("autostart")

__all__ = ["FLAG", "command", "is_supported", "sync", "task_xml"]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "WinDuo2"
TASK_NAME = "WinDuo2"

#: Passed on the command line at sign-in, so a start Windows made can be told
#: apart from one the user made. Nothing opens on its own at sign-in.
FLAG = "--autostart"


def is_supported() -> bool:
    return sys.platform == "win32"


def _program() -> tuple[Path, list[str]]:
    """The executable and arguments that start this copy of WinDuo2 at sign-in."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        # The packaged WinDuo2.exe. Written fresh on every start, so moving the
        # file and running it once from its new place keeps the entry working.
        return executable, [FLAG]
    # From source: pythonw rather than python, so no console window opens.
    windowed = executable.with_name("pythonw.exe")
    if windowed.exists():
        executable = windowed
    return executable, ["-m", "winduo2", FLAG]


def command() -> str:
    """The same program as one command line, for the ``Run`` key."""
    executable, arguments = _program()
    return " ".join([f'"{executable}"', *arguments])


def _user() -> str:
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def task_xml(user: str | None = None) -> str:
    """The task definition: start at this user's sign-in, and keep running."""
    user = _user() if user is None else user
    executable, arguments = _program()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts WinDuo2 when you sign in.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(executable))}</Command>
      <Arguments>{escape(" ".join(arguments))}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def sync(enabled: bool) -> None:
    """Make the sign-in start match ``enabled``.

    Shells out to ``schtasks``, which takes a moment, so call it off the thread
    that is bringing the app up.
    """
    if not is_supported():
        return
    if enabled:
        if _register_task():
            _set_run_entry(False)
        else:
            log.warning("could not register the sign-in task, using the Run key instead")
            _set_run_entry(True)
    else:
        _delete_task()
        _set_run_entry(False)


def _schtasks(*arguments: str) -> bool:
    try:
        result = subprocess.run(
            ["schtasks", *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            # No console window flashing up from a windowed app.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("could not run schtasks", exc_info=True)
        return False
    if result.returncode != 0:
        log.info("schtasks %s: %s", arguments[0], (result.stderr or result.stdout).strip())
    return result.returncode == 0


def _register_task() -> bool:
    handle, name = tempfile.mkstemp(suffix=".xml")
    os.close(handle)
    path = Path(name)
    try:
        path.write_text(task_xml(), encoding="utf-16")
        return _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(path), "/F")
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def _delete_task() -> None:
    _schtasks("/Delete", "/TN", TASK_NAME, "/F")


def _set_run_entry(enabled: bool) -> None:
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
