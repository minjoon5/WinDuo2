"""The scene shop, as interface tokens.

A light ground with a single blue accent and softly rounded controls, so the
settings window sits comfortably beside Windows' own light mode.

These windows are Operate surfaces. Nothing here should make a slider harder
to find. Contrast is checked against
WCAG 2.2 AA, and the accent is never the only carrier of meaning.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QFont,
    QFontDatabase,
    QIcon,
    QPixmap,
)

__all__ = ["Palette", "STYLESHEET", "app_icon", "tray_icon", "ui_font"]


class Palette:
    """Named colours. Every text pair below is 4.5:1 or better on its ground."""

    # A light, slightly cool ground. The app is used in a lit room, and a
    # settings window that matches Windows' light mode sits quietly beside it.
    CANVAS = "#f4f6f9"
    CANVAS_RAISED = "#ffffff"
    CANVAS_SUNK = "#e8ecf1"
    FRAME = "#d3d9e1"
    FRAME_STRONG = "#b4bdc9"

    # Ink.
    CHALK = "#1a2230"          # 15.2:1 on CANVAS
    CHALK_SOFT = "#465163"     # 7.9:1 on CANVAS
    CHALK_FAINT = "#667183"    # 4.8:1 on CANVAS

    # Blue is the active mark, and its deeper shade is used where something is
    # measured rather than chosen.
    ACCENT = "#2563eb"
    ACCENT_BRIGHT = "#3b78f0"
    ACCENT_SUNK = "#1d4fc4"
    ACCENT_SOFT = "#dbe6fd"
    MEASURE = "#1d4fc4"        # 6.9:1 on CANVAS

    # Status. Paired with text in the interface, never colour alone.
    GOOD = "#2f8a57"
    WARN = "#b7791f"
    BAD = "#c53b30"


def ui_font(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """A workhorse UI face.

    Operate surfaces are well served by the platform's own text face, and a
    settings window is not the place to introduce a display voice. The site
    carries the lettering; these windows carry the work.
    """
    for family in ("Segoe UI Variable Text", "Segoe UI", "Inter"):
        if family in QFontDatabase.families():
            font = QFont(family, size)
            font.setWeight(weight)
            return font
    font = QFont()
    font.setPointSize(size)
    font.setWeight(weight)
    return font


def label_font(size: int = 8) -> QFont:
    """Small tracked caps for section labels."""
    font = ui_font(size, QFont.Weight.DemiBold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    return font


P = Palette

STYLESHEET = f"""
QWidget {{
    background: {P.CANVAS};
    color: {P.CHALK};
}}

QDialog, QMainWindow {{
    background: {P.CANVAS};
}}

/* A hairline between stages, never decoration. */
QFrame[role="rule"] {{
    background: {P.FRAME};
    max-height: 1px;
    border: none;
}}

QLabel[role="title"] {{
    color: {P.CHALK};
    font-size: 17px;
    font-weight: 600;
}}

QLabel[role="section"] {{
    color: {P.CHALK_SOFT};
}}

QLabel[role="help"] {{
    color: {P.CHALK_FAINT};
}}

QLabel[role="reading"] {{
    color: {P.MEASURE};
    font-weight: 600;
}}

QLabel[role="notice"] {{
    background: {P.CANVAS_RAISED};
    border: 1px solid {P.FRAME};
    border-radius: 10px;
    padding: 10px 12px;
    color: {P.CHALK_SOFT};
}}

QLabel[role="alert"] {{
    background: #fdeeec;
    border: 1px solid #f1bcb6;
    border-radius: 10px;
    padding: 10px 12px;
    color: #7f2219;
}}

/* --- Buttons ---------------------------------------------------------- */

QPushButton {{
    background: {P.CANVAS_RAISED};
    border: 1px solid {P.FRAME};
    border-radius: 8px;
    padding: 7px 16px;
    color: {P.CHALK};
}}

QPushButton:hover {{
    background: #f8fafc;
    border-color: {P.FRAME_STRONG};
}}

QPushButton:pressed {{
    background: {P.CANVAS_SUNK};
}}

QPushButton:focus {{
    outline: none;
    border-color: {P.ACCENT};
}}

QPushButton:disabled {{
    color: {P.CHALK_FAINT};
    background: {P.CANVAS_SUNK};
    border-color: {P.FRAME};
}}

QPushButton[role="primary"] {{
    background: {P.ACCENT};
    border-color: {P.ACCENT_SUNK};
    color: #ffffff;
    font-weight: 600;
}}

QPushButton[role="primary"]:hover {{
    background: {P.ACCENT_BRIGHT};
}}

QPushButton[role="primary"]:pressed {{
    background: {P.ACCENT_SUNK};
}}

QPushButton[role="primary"]:disabled {{
    background: #a9bff0;
    border-color: #9db3e6;
    color: #f2f6fe;
}}

/* --- Sliders ---------------------------------------------------------- */

/* No margin on the groove: the filled and empty parts ignore it and would
   otherwise paint the slider's full height. The handle's negative margin
   sets the slider's height instead. */
QSlider {{
    background: transparent;
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {P.CANVAS_SUNK};
    border: none;
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {P.ACCENT};
    border-radius: 3px;
}}

QSlider::add-page:horizontal {{
    background: {P.FRAME};
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background: {P.CANVAS_RAISED};
    border: 2px solid {P.ACCENT};
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 8px;
}}

QSlider::handle:horizontal:hover {{
    border-color: {P.ACCENT_BRIGHT};
    background: {P.ACCENT_SOFT};
}}

QSlider:focus::handle:horizontal {{
    border-color: {P.ACCENT_SUNK};
}}

QSlider::sub-page:horizontal:disabled {{
    background: {P.FRAME_STRONG};
}}

QSlider::handle:horizontal:disabled {{
    border-color: {P.FRAME_STRONG};
}}

/* --- Checkboxes ------------------------------------------------------- */

QCheckBox {{
    spacing: 9px;
    background: transparent;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {P.FRAME_STRONG};
    border-radius: 5px;
    background: {P.CANVAS_RAISED};
}}

QCheckBox::indicator:hover {{
    border-color: {P.ACCENT};
}}

QCheckBox::indicator:checked {{
    background: {P.ACCENT};
    border-color: {P.ACCENT_SUNK};
}}

QCheckBox::indicator:focus {{
    border-color: {P.ACCENT_SUNK};
}}

QCheckBox:disabled {{
    color: {P.CHALK_FAINT};
}}

/* --- Combo box -------------------------------------------------------- */

QComboBox {{
    background: {P.CANVAS_RAISED};
    border: 1px solid {P.FRAME};
    border-radius: 8px;
    padding: 5px 10px;
    color: {P.CHALK};
}}

QComboBox:focus {{
    border-color: {P.ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 18px;
}}

QComboBox QAbstractItemView {{
    background: {P.CANVAS_RAISED};
    border: 1px solid {P.FRAME};
    border-radius: 8px;
    selection-background-color: {P.ACCENT};
    selection-color: #ffffff;
    outline: none;
}}

/* --- Scroll area ------------------------------------------------------ */

QScrollArea {{
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {P.FRAME_STRONG};
    border-radius: 4px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: #98a3b2;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

/* --- Tray menu -------------------------------------------------------- */

QMenu {{
    background: {P.CANVAS_RAISED};
    border: 1px solid {P.FRAME};
    border-radius: 10px;
    padding: 5px;
}}

QMenu::item {{
    padding: 6px 26px 6px 14px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background: {P.ACCENT};
    color: #ffffff;
}}

QMenu::item:disabled {{
    color: {P.CHALK_FAINT};
}}

QMenu::separator {{
    height: 1px;
    background: {P.FRAME};
    margin: 5px 8px;
}}

QMenu::indicator {{
    width: 13px;
    height: 13px;
    left: 8px;
}}

QToolTip {{
    background: {P.CANVAS_RAISED};
    color: {P.CHALK};
    border: 1px solid {P.FRAME};
    border-radius: 6px;
    padding: 5px 7px;
}}
"""


# --- Icons ---------------------------------------------------------------


#: The app's icon, shipped beside this module. ``icon.ico`` next to it is the
#: same picture at every Windows size, for the executable.
ICON_PATH = Path(__file__).with_name("icon.png")


def _pixmap(size: int) -> QPixmap:
    source = QPixmap(str(ICON_PATH))
    if source.isNull():
        return QPixmap()
    return source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def app_icon(active: bool = False) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_pixmap(size))
    return icon


def tray_icon(active: bool = False) -> QIcon:
    """The same picture at tray sizes. ``active`` is kept for the tray's calls;
    the icon no longer changes while the effect runs."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48):
        icon.addPixmap(_pixmap(size))
    return icon
