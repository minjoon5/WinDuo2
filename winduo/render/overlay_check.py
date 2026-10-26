"""Finds which overlay window setting stops the effect from showing on a machine.

``python -m winduo --overlay-check`` shows a half-transparent red tint over the
whole screen several times, each with one more of the settings the real overlay
uses. The first step that no longer shows red names the setting at fault.
"""

from __future__ import annotations

from OpenGL import GL
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QWidget

from winduo.render import win32
from winduo.render.overlay import flip_safe_geometry

__all__ = ["run_overlay_check"]

#: Each step adds one setting to the one before.
STEPS = (
    ("plain topmost window", {}),
    ("+ translucent background", {"translucent": True}),
    ("+ window opacity animation", {"translucent": True, "opacity": True}),
    ("+ click-through styles", {"translucent": True, "opacity": True, "click_through": True}),
    (
        "+ hidden from screen capture (the real overlay)",
        {"translucent": True, "opacity": True, "click_through": True, "exclude": True},
    ),
)

SECONDS_PER_STEP = 3.0


class _Tint(QOpenGLWidget):
    def paintGL(self) -> None:  # noqa: N802 - Qt naming
        GL.glClearColor(0.5, 0.0, 0.0, 0.5)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)


def _show(step: dict) -> QWidget:
    screen = QGuiApplication.primaryScreen()
    rect = screen.geometry()
    window = QWidget(
        None,
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
        | Qt.WindowType.WindowTransparentForInput
        | Qt.WindowType.BypassWindowManagerHint,
    )
    if step.get("translucent"):
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    if step.get("opacity"):
        window.setWindowOpacity(0.0)
    window.setGeometry(flip_safe_geometry(rect))
    view = _Tint(window)
    view.setGeometry(0, 0, rect.width(), rect.height())
    window.show()
    if step.get("exclude"):
        win32.exclude_from_capture(window, True)
    if step.get("click_through"):
        win32.make_click_through(window)
    win32.raise_topmost(window)
    if step.get("opacity"):
        # The same route the real overlay takes from invisible to shown.
        QTimer.singleShot(300, lambda: window.setWindowOpacity(1.0))
    return window


def run_overlay_check(app) -> int:
    print("Watch the screen. Each step tints it red for 3 seconds.")
    print("Note the numbers of the steps where you saw red.\n")
    state = {"index": 0, "window": None}

    def advance() -> None:
        window = state["window"]
        if window is not None:
            window.hide()
            window.deleteLater()
        index = state["index"]
        if index >= len(STEPS):
            print("\nDone. Send the step numbers where the screen turned red.")
            app.quit()
            return
        name, step = STEPS[index]
        print(f"Step {index + 1}: {name}", flush=True)
        state["window"] = _show(step)
        state["index"] = index + 1
        QTimer.singleShot(int(SECONDS_PER_STEP * 1000), advance)

    QTimer.singleShot(500, advance)
    return app.exec()
