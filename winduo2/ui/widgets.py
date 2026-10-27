"""Small shared widgets, built in the scene-shop vocabulary.

The hold meter is the one that carries real information rather than
decoration: how far to trust the current lid reading.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from winduo2.ui.theme import Palette, label_font, ui_font

__all__ = ["HoldMeter", "Rule", "SectionLabel", "SliderRow", "help_label"]


class _ScrollSafeSlider(QSlider):
    """A slider that refuses the wheel, so scrolling past it scrolls the page.

    A ``QSlider`` accepts wheel events by default, which means a settings panel
    inside a scroll area changes whatever value happens to be under the cursor
    every time someone scrolls past it. Ignoring the event here does not
    discard it: an ignored event propagates to the parent widget, which is the
    scroll area, so the page keeps scrolling exactly as if the slider were not
    there. Dragging and the arrow keys are unaffected.
    """

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class Rule(QFrame):
    """A struck chalk line. Structure, not ornament."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "rule")
        self.setFixedHeight(1)
        self.setFrameShape(QFrame.Shape.NoFrame)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "section")
        self.setFont(label_font(8))


def help_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "help")
    label.setWordWrap(True)
    label.setFont(ui_font(8))
    return label


class SliderRow(QWidget):
    """A labelled slider with a live reading and one line of explanation.

    Sliders carry integers internally and a scale factor out, because Qt sliders
    are integer-only and the settings are not.
    """

    changed = pyqtSignal(float)

    def __init__(
        self,
        title: str,
        minimum: float,
        maximum: float,
        value: float,
        suffix: str = "",
        decimals: int = 0,
        display_scale: float = 1.0,
        help_text: str = "",
        steps: int = 200,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._steps = steps
        self._suffix = suffix
        self._decimals = decimals
        self._display_scale = display_scale
        #: The value last set from outside, shown as is until the slider moves,
        #: so a default between two steps does not read as its neighbour.
        self._exact: float | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(title)
        self._title.setFont(ui_font(9))
        self._reading = QLabel()
        self._reading.setProperty("role", "reading")
        self._reading.setFont(ui_font(9, weight=ui_font().weight()))
        self._reading.setAlignment(Qt.AlignmentFlag.AlignRight)
        # Reserve the widest reading so the row does not jitter as it changes.
        widest = self._format(self._maximum)
        self._reading.setMinimumWidth(
            QFontMetrics(self._reading.font()).horizontalAdvance(widest) + 6
        )
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self._reading)
        layout.addLayout(head)

        self._slider = _ScrollSafeSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, steps)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(max(steps // 20, 1))
        self._slider.setAccessibleName(title)
        self._slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self._slider)

        if help_text:
            layout.addWidget(help_label(help_text))

        self.set_value(value)

    def _format(self, value: float) -> str:
        return f"{value * self._display_scale:.{self._decimals}f}{self._suffix}"

    def value(self) -> float:
        if self._exact is not None:
            return self._exact
        span = self._maximum - self._minimum
        return self._minimum + span * (self._slider.value() / self._steps)

    def set_value(self, value: float) -> None:
        span = self._maximum - self._minimum or 1.0
        fraction = (float(value) - self._minimum) / span
        position = int(round(min(max(fraction, 0.0), 1.0) * self._steps))
        blocked = self._slider.blockSignals(True)
        self._slider.setValue(position)
        self._slider.blockSignals(blocked)
        self._exact = float(value)
        self._refresh()

    def _on_slider(self, _position: int) -> None:
        self._exact = None
        self._refresh()
        self.changed.emit(self.value())

    def _refresh(self) -> None:
        reading = self._format(self.value())
        self._reading.setText(reading)
        self._slider.setAccessibleDescription(reading)


class HoldMeter(QWidget):
    """Fills while the lid is held still, left to right."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._steady = False
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_progress(self, progress: float, steady: bool = False) -> None:
        progress = min(max(float(progress), 0.0), 1.0)
        if abs(progress - self._progress) < 0.005 and steady == self._steady:
            return
        self._progress = progress
        self._steady = steady
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(Palette.FRAME))
        painter.drawRoundedRect(QRectF(0, 1, self.width(), 2), 1, 1)
        if self._progress <= 0:
            return
        painter.setBrush(
            QColor(Palette.GOOD if self._steady else Palette.ACCENT)
        )
        painter.drawRoundedRect(
            QRectF(0, 0, self.width() * self._progress, 4), 2, 2
        )
