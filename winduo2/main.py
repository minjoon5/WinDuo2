"""Entry point.

``python -m winduo2`` starts the app. ``--preview`` plays the effect once and
exits, which is the quickest way to see whether the renderer works on a machine
without involving the lid at all. ``--sensors`` does the opposite: it prints
what the lid sensor reads, with no window, to check that this machine has one.
"""

from __future__ import annotations

import argparse
import signal
import sys

from winduo2 import __version__
from winduo2.log import get_logger, setup_logging

log = get_logger("main")

__all__ = ["main"]


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="winduo2",
        description="Lean the screen away as the laptop lid closes.",
    )
    parser.add_argument("--version", action="version", version=f"WinDuo2 {__version__}")
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

    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        print(
            "WinDuo2 runs on Windows, and on Linux under Hyprland.",
            file=sys.stderr,
        )
        return 2
    if options.sensors:
        return _print_sensor()
    if sys.platform.startswith("linux"):
        import os

        # The overlay has to be a native Wayland window for Hyprland to float
        # and pin it; under XWayland it would be an X11 window it cannot place.
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")

    # Imported here so --version and the platform check cost nothing.
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from winduo2.app import Engine
    from winduo2.config import Store
    from winduo2.render.overlay import configure_surface
    from winduo2.ui.theme import STYLESHEET, app_icon, ui_font
    from winduo2.ui.tray import TrayController

    # Must happen before any window exists, or the context is already chosen.
    configure_surface()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("WinDuo2")
    app.setApplicationDisplayName("WinDuo2")
    app.setApplicationVersion(__version__)
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

    log.info("WinDuo2 %s ready", __version__)
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

    QTimer.singleShot(5200, finish)
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


def _follow_launch_at_login(store) -> None:
    """Keep the Windows startup entry in step with the setting."""
    from winduo2 import autostart

    applied = store.settings.launch_at_login
    # Written on every start, not just when the box is ticked, so the entry
    # follows the exe if it has moved since.
    autostart.sync(applied)

    def changed() -> None:
        nonlocal applied
        wanted = store.settings.launch_at_login
        if wanted != applied:
            applied = wanted
            autostart.sync(wanted)

    store.subscribe(changed)


if __name__ == "__main__":
    raise SystemExit(main())
