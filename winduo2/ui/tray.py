"""The tray icon, which owns the application.

There is no main window. The effect happens on the screen you are already
looking at, so a window would only ever be in the way.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from winduo2.config import Store
from winduo2.log import get_logger
from winduo2.ui.settings import SettingsWindow
from winduo2.ui.theme import tray_icon

log = get_logger("tray")

__all__ = ["TrayController"]


class TrayController(QObject):
    def __init__(self, store: Store, engine) -> None:
        super().__init__()
        self.store = store
        self.engine = engine

        self._settings: SettingsWindow | None = None
        self._was_active = False

        self.icon = QSystemTrayIcon(tray_icon(False))
        self.icon.setToolTip("WinDuo2")
        self.icon.activated.connect(self._on_activated)
        self.icon.setContextMenu(self._build_menu())

        engine.changed.connect(self._refresh)
        self._tooltip = QTimer(self)
        self._tooltip.setInterval(400)
        self._tooltip.timeout.connect(self._refresh)

    def show(self, introduce: bool = True) -> None:
        self.icon.show()
        self._tooltip.start()
        self._refresh()

        if introduce and not self.store.settings.has_seen_intro:
            self._introduce()

    # --- Menu ------------------------------------------------------------

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self._effect_action = QAction("Run the effect", menu)
        self._effect_action.setCheckable(True)
        self._effect_action.setChecked(self.store.settings.enabled)
        self._effect_action.toggled.connect(
            lambda value: self.store.update(enabled=value)
        )
        menu.addAction(self._effect_action)

        preview = QAction("Preview once", menu)
        preview.triggered.connect(self.engine.run_preview)
        menu.addAction(preview)

        menu.addSeparator()

        settings = QAction("Settings\u2026", menu)
        settings.triggered.connect(self.open_settings)
        menu.addAction(settings)

        menu.addSeparator()

        self._status_action = QAction("", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)

        menu.addSeparator()

        quit_action = QAction("Quit WinDuo2", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self._menu = menu
        return menu

    # --- Windows ---------------------------------------------------------

    def open_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(self.store, self.engine)
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _introduce(self) -> None:
        """First run: say where the app lives, then show its controls once.

        The app has no main window, and a tray icon and a balloon are both easy
        to miss, so without the settings window the effect would just happen
        and every slider in it would stay undiscovered.
        """
        self.store.update(has_seen_intro=True)
        self.icon.showMessage(
            "WinDuo2 is ready",
            "Close the lid and the screen will lean away. WinDuo2 lives in the "
            "notification area; right-click it for settings.",
            tray_icon(True),
            6000,
        )
        QTimer.singleShot(700, self.open_settings)

    # --- Status ----------------------------------------------------------

    def _refresh(self) -> None:
        settings = self.store.settings
        self._effect_action.setChecked(settings.enabled)

        active = self.engine.is_effect_running
        if active != self._was_active:
            self._was_active = active
            self.icon.setIcon(tray_icon(active))

        problem = self.engine.problem
        if not settings.enabled:
            summary = "Effect off"
        elif problem:
            summary = problem
        elif active:
            summary = "Running"
        else:
            sample = self.engine.sensor.latest(max_age=1.0)
            if sample is None:
                summary = "Waiting for the lid sensor"
            elif sample.at_rest:
                summary = "Resting, ready"
            else:
                summary = f"{max(sample.travel, 0.0):.0f}\u00b0 from resting"

        self._status_action.setText(summary)
        if settings.show_reading:
            self.icon.setToolTip(f"WinDuo2 \u2014 {summary}")
        else:
            self.icon.setToolTip("WinDuo2")

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_settings()

    def _quit(self) -> None:
        self._tooltip.stop()
        self.engine.stop()
        self.icon.hide()
        QApplication.quit()
