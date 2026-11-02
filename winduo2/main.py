"""Entry point.

``python -m winduo2`` starts the app. ``--preview`` plays the effect once and
exits, which is the quickest way to see whether the renderer works on a machine
without involving the lid at all. ``--sensors`` does the opposite: it prints
what the lid sensor reads, with no window, to check that this machine has one.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from winduo2.log import get_logger, setup_logging

log = get_logger("main")

__all__ = ["main"]


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="winduo2",
        description="Lean the screen away as the laptop lid closes.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="play the effect once and exit, without using the lid sensor",
    )
    parser.add_argument(
        "--sensors",
        action="store_true",
        help="print the lid sensor's readings for 20 seconds and exit",
    )
    parser.add_argument(
        "--overlay-check",
        action="store_true",
        help="tint the screen with each overlay window setting in turn, to find one that fails",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="open the settings window at launch",
    )
    # Written into the startup entry, not typed: see ``autostart.FLAG``.
    parser.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-v", "--verbose", action="store_true", help="log more")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = _parse(argv)
    setup_logging(options.verbose)
    log.info(
        "WinDuo2 starting, pid %d%s",
        os.getpid(),
        " (started at sign-in)" if options.autostart else "",
    )
    hang_report = _watch_for_a_hung_start()

    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        print(
            "WinDuo2 runs on Windows, and on Linux under Hyprland.",
            file=sys.stderr,
        )
        return 2
    if options.sensors:
        return _print_sensor()
    if sys.platform.startswith("linux"):
        # The overlay has to be a native Wayland window for Hyprland to float
        # and pin it; under XWayland it would be an X11 window it cannot place.
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")

    # Imported here so --help and the platform check cost nothing.
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from winduo2.app import Engine
    from winduo2.config import Store
    from winduo2.render.overlay import configure_surface
    from winduo2.ui.theme import STYLESHEET, app_icon, ui_font
    from winduo2.ui.tray import TrayController

    if options.autostart:
        _wait_for_the_shell()

    # Must happen before any window exists, or the context is already chosen.
    configure_surface()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("WinDuo2")
    app.setApplicationDisplayName("WinDuo2")
    app.setOrganizationName("WinDuo2")
    app.setWindowIcon(app_icon())
    app.setStyleSheet(STYLESHEET)
    app.setFont(ui_font(9))
    # The tray owns the lifetime. Closing the last window must not quit.
    app.setQuitOnLastWindowClosed(False)

    if options.overlay_check:
        from winduo2.render.overlay_check import run_overlay_check

        return run_overlay_check(app)

    store = Store()
    engine = Engine(store)
    _follow_launch_at_login(store)

    if options.preview:
        return _run_preview(app, engine)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "WinDuo2",
            "Windows is not offering a notification area, so there would be no "
            "way to reach WinDuo2's controls.",
        )
        return 3

    tray = TrayController(store, engine)
    engine.start()
    # Windows starting the app at sign-in must not put a window in front of
    # whatever the user signed in to do, first run or not.
    tray.show(introduce=not options.autostart)

    if options.settings:
        QTimer.singleShot(0, tray.open_settings)

    # Ctrl+C from a terminal, which Qt otherwise swallows.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.start(250)
    heartbeat.timeout.connect(lambda: None)

    from winduo2.render import win32

    log.info(
        "WinDuo2 ready%s%s%s",
        _since_process_start(),
        _since_boot(),
        ", with uiAccess" if win32.has_ui_access() else "",
    )
    _stop_watching(hang_report)
    code = app.exec()
    engine.stop()
    return code


def _run_preview(app, engine) -> int:
    """Play the scripted sweep once, then quit. No sensor, no lid."""
    from PyQt6.QtCore import QTimer

    engine.start()
    QTimer.singleShot(400, engine.run_preview)

    def finish() -> None:
        engine.stop()
        app.quit()

    QTimer.singleShot(8800, finish)
    log.info("running the preview sweep")
    return app.exec()


def _print_sensor(seconds: float = 20.0) -> int:
    """Show what the lid sensor reports. Close and open the lid to watch it move."""
    import time

    from winduo2.angle.sensor import SensorAngleSource
    from winduo2.config import Settings

    source = SensorAngleSource(Settings())
    source.start()
    print("Looking for a lid sensor. Move the lid to see the readings change.")
    ends = time.monotonic() + seconds
    shown = ""
    try:
        while time.monotonic() < ends:
            time.sleep(0.2)
            status = source.status()
            if status.problem:
                print(status.problem)
                return 1
            if status.name and status.name != shown:
                shown = status.name
                print(f"Using the {shown}.")
            sample = source.latest()
            if sample is not None:
                print(
                    f"angle {sample.absolute_angle:6.1f}\u00b0   "
                    f"travel {sample.travel:6.1f}\u00b0   "
                    f"speed {sample.velocity:7.1f}\u00b0/s   "
                    f"confidence {sample.confidence:4.2f}   "
                    f"{'resting' if sample.at_rest else 'moving'}   "
                    f"{status.rate:3.0f}/s"
                )
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
    return 0


def _since_boot() -> str:
    """How long the machine has been up, for the log: at sign-in it says how
    long WinDuo2 took to come up."""
    if sys.platform != "win32":
        return ""
    import ctypes

    getter = ctypes.windll.kernel32.GetTickCount64
    getter.restype = ctypes.c_ulonglong
    return f", {getter() / 1000.0:.0f} s after the machine started"


def _since_process_start() -> str:
    """How long this process took to get here, bootloader and imports included."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    created, exited, kernel, user, now = (wintypes.FILETIME() for _ in range(5))
    if not kernel32.GetProcessTimes(
        kernel32.GetCurrentProcess(),
        ctypes.byref(created), ctypes.byref(exited),
        ctypes.byref(kernel), ctypes.byref(user),
    ):
        return ""
    kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))

    def ticks(t):
        return (t.dwHighDateTime << 32) | t.dwLowDateTime

    return f" in {(ticks(now) - ticks(created)) / 1e7:.1f} s"


#: Longest the sign-in start waits for the taskbar before going ahead anyway.
SHELL_WAIT = 30.0


def _wait_for_the_shell(timeout: float = SHELL_WAIT) -> None:
    """At sign-in, hold the first window back until the taskbar is up.

    Task Scheduler starts WinDuo2 the moment the user signs in, often before
    Explorer has built the taskbar and its notification area. The installed
    uiAccess build once hung for good when it made its windows that early. A
    fixed delay would cost every sign-in that long; this waits only as long as
    the shell actually takes, which is nothing once it is up. The imports above
    have already run by now, so their time overlaps with the shell's.
    """
    if sys.platform != "win32":
        return
    import ctypes
    import time

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowExW.restype = ctypes.c_void_p
    user32.FindWindowExW.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p
    ]

    def ready() -> bool:
        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        return bool(taskbar and user32.FindWindowExW(taskbar, None, "TrayNotifyWnd", None))

    started = time.monotonic()
    while not ready():
        if time.monotonic() - started >= timeout:
            log.warning("the taskbar is not up after %.0f s; starting anyway", timeout)
            return
        time.sleep(0.1)
    waited = time.monotonic() - started
    if waited >= 0.1:
        log.info("waited %.1f s for the taskbar", waited)


#: A start that has not reached "ready" in this long has hung somewhere.
HANG_TIMEOUT = 60.0


def _watch_for_a_hung_start():
    """Dump every thread's stack to a file if startup never finishes.

    A start that hangs before the first window, as the installed build once did
    at sign-in, leaves no other trace: nothing reaches the log and the windowed
    exe has no console. ``faulthandler`` writes from its own thread, so it works
    even when the main thread is stuck inside native code. Returns the open
    file, to be handed to :func:`_stop_watching`, or None.
    """
    import faulthandler

    from winduo2.log import APP_DIR

    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        report = open(APP_DIR / "winduo2-hang.txt", "w", encoding="utf-8")  # noqa: SIM115
        faulthandler.dump_traceback_later(HANG_TIMEOUT, repeat=False, file=report)
    except (OSError, ValueError, RuntimeError):
        log.debug("could not watch for a hung start", exc_info=True)
        return None
    return report


def _stop_watching(report) -> None:
    """Startup finished: cancel the dump and drop the empty report."""
    import contextlib
    import faulthandler

    if report is None:
        return
    faulthandler.cancel_dump_traceback_later()
    path = report.name
    report.close()
    with contextlib.suppress(OSError):
        if os.path.getsize(path) == 0:
            os.remove(path)


def _follow_launch_at_login(store) -> None:
    """Keep the Windows startup entry in step with the setting.

    On a worker thread of its own: registering the sign-in task shells out to
    ``schtasks``, and the tray should not wait for it. One worker, so changes
    are applied in the order they were made.
    """
    from concurrent.futures import ThreadPoolExecutor

    from winduo2 import autostart

    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="winduo2-autostart")
    applied = store.settings.launch_at_login
    # Written on every start, not just when the box is ticked, so the entry
    # follows the exe if it has moved since.
    worker.submit(autostart.sync, applied)

    def changed() -> None:
        nonlocal applied
        wanted = store.settings.launch_at_login
        if wanted != applied:
            applied = wanted
            worker.submit(autostart.sync, wanted)

    store.subscribe(changed)


if __name__ == "__main__":
    raise SystemExit(main())
