"""Finds which overlay window setting stops the effect from showing on a machine.

``python -m winduo --overlay-check`` shows a half-transparent red tint over the
whole screen several times, each with one more of the settings the real overlay
uses. The first step that no longer shows red names the setting at fault. The
last two steps run the real overlay and shader, first on a test pattern and then
on the captured screen, to split drawing problems from capture problems.
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


def _real_overlay(use_capture: bool):
    """The effect's own window and shader, holding a mid-close frame.

    Returns a cleanup function, and prints what the overlay reports about
    itself, so the result does not rest on what was seen alone.
    """
    import numpy as np

    from winduo.config import Settings
    from winduo.effect.controller import EffectController, Frame
    from winduo.render.capture import CaptureError, ScreenCapture
    from winduo.render.overlay import DepthOverlay, FrameParams

    screen = QGuiApplication.primaryScreen()
    rect = screen.geometry()
    ratio = screen.devicePixelRatio()
    native = (float(rect.width()) * ratio, float(rect.height()) * ratio)

    capture = None
    if use_capture:
        try:
            capture = ScreenCapture(monitor=0, fps=60, width_cap=1600)
            capture.start()
        except CaptureError as error:
            print(f"  capture failed: {error}")
            return lambda: None
        native = (float(capture.native_size[0]), float(capture.native_size[1]))
        picture_size = capture.size
        print(f"  capturing {capture.native_size} as {capture.size}")
    else:
        picture_size = (int(native[0]) // 2 // 2 * 2, int(native[1]) // 2 // 2 * 2)
        stripes = (np.arange(picture_size[0]) // 40 % 2).astype(np.uint8) * 200 + 30
        pattern = np.empty((picture_size[1], picture_size[0], 4), dtype=np.uint8)
        pattern[..., 0] = stripes
        pattern[..., 1] = 120
        pattern[..., 2] = stripes[::-1]
        pattern[..., 3] = 255

    settings = Settings()
    controller = EffectController(settings)
    frame = Frame(start_angle=90.0, current_angle=40.0, progress=0.6)
    overlay = DepthOverlay()
    print(f"  warm up: {overlay.warm_up(rect, native, picture_size)}")
    print(f"  show: {overlay.show(rect, native, picture_size)}")
    gradient = controller.gradient
    params = FrameParams(
        profile=controller.profile(frame, native),
        blur_strength=gradient.blur_strength(frame.progress),
        dim_strength=gradient.dim_strength(frame.progress),
        turn_strength=gradient.turn_strength(frame.progress),
        dim_floor=gradient.dim_hinge_floor,
        dim_reach=settings.dim_reach,
        max_blur_radius=settings.max_blur_radius,
        max_dim=settings.max_dim,
        hinge_glow=settings.hinge_glow,
        reflection_intensity=settings.reflection_intensity,
    )
    sent = {"frames": 0}

    def tick() -> None:
        picture = capture.take() if capture is not None else pattern
        if picture is not None:
            sent["frames"] += 1
        overlay.submit(picture, params)

    timer = QTimer()
    timer.setInterval(16)
    timer.timeout.connect(tick)
    timer.start()

    def cleanup() -> None:
        window = overlay._window
        print(
            f"  frames sent {sent['frames']}, has picture {overlay.has_picture}, "
            f"window opacity {window.windowOpacity() if window else 'none'}"
        )
        timer.stop()
        overlay.destroy()
        if capture is not None:
            capture.stop()

    return cleanup


def run_overlay_check(app) -> int:
    print("Watch the screen. Steps 1 to 5 tint it red for 3 seconds each;")
    print("steps 6 and 7 show the real effect, tilted back and darkened.")
    print("Note the numbers of the steps where you saw something.\n")
    steps = [(name, lambda step=step: _tint_step(step)) for name, step in STEPS]
    steps.append(("real effect window, test pattern", lambda: _real_overlay(False)))
    steps.append(("real effect window, captured screen", lambda: _real_overlay(True)))
    state = {"index": 0, "cleanup": None}

    def advance() -> None:
        cleanup = state["cleanup"]
        if cleanup is not None:
            cleanup()
        index = state["index"]
        if index >= len(steps):
            print("\nDone. Send the step numbers where you saw something, and this output.")
            app.quit()
            return
        name, start = steps[index]
        print(f"Step {index + 1}: {name}", flush=True)
        state["cleanup"] = start()
        state["index"] = index + 1
        QTimer.singleShot(int(SECONDS_PER_STEP * 1000), advance)

    QTimer.singleShot(500, advance)
    return app.exec()


def _tint_step(step: dict):
    window = _show(step)

    def cleanup() -> None:
        window.hide()
        window.deleteLater()

    return cleanup
