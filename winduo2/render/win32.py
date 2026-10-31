"""The Win32 calls that make an overlay window behave like an overlay.

Qt can produce a frameless always-on-top translucent window on its own, but
three things it has no API for decide whether this effect works at all:

- **Excluding the window from screen capture.** Without it the overlay is
  captured, drawn into itself, and recurses. This is the Windows equivalent of
  ScreenCaptureKit's application exclusion, and Mac Duo needs the same thing.
- **Click-through.** ``WS_EX_TRANSPARENT`` sends hit-testing straight past the
  window, so the applications underneath keep working normally.
- **Staying above full-screen windows.** ``HWND_TOPMOST`` without activation.

Topmost is not the top, though. The pointer is drawn by the display hardware
after composition, and the Start menu, search and the notification flyouts
live in z-order bands above every topmost window that only a signed
``uiAccess`` process can reach. The pointer is therefore hidden, and those
flyouts are closed, when the effect starts.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from winduo2.log import get_logger

log = get_logger("win32")

__all__ = [
    "exclude_from_capture",
    "make_click_through",
    "raise_topmost",
    "is_windows",
    "hide_pointer",
    "restore_pointer",
    "close_shell_flyouts",
    "MIN_BUILD_FOR_CAPTURE_EXCLUSION",
]

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080

_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040

_WDA_NONE = 0x00000000
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

#: ``WDA_EXCLUDEFROMCAPTURE`` arrived in Windows 10 version 2004. On older
#: builds the call succeeds but behaves like ``WDA_MONITOR``, which blanks the
#: window in captures instead of removing it. There is no workaround.
MIN_BUILD_FOR_CAPTURE_EXCLUSION = 19041


def is_windows() -> bool:
    return sys.platform == "win32"


def _hwnd(window) -> int | None:
    """Pull an HWND out of a Qt window handle."""
    try:
        handle = int(window.winId())
    except (AttributeError, TypeError, ValueError):
        return None
    return handle or None


def exclude_from_capture(window, enabled: bool = True) -> bool:
    """Hide the window from every screen capture API while leaving it on screen.

    Returns ``False`` when the platform cannot do it, which the caller should
    treat as a reason to fall back to a held frame rather than a live one:
    capturing a live screen that contains this window feeds it back into itself.
    """
    if not is_windows():
        return False
    handle = _hwnd(window)
    if handle is None:
        return False
    affinity = _WDA_EXCLUDEFROMCAPTURE if enabled else _WDA_NONE
    ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(
        wintypes.HWND(handle), wintypes.DWORD(affinity)
    ))
    if not ok:
        log.warning(
            "could not exclude the overlay from capture (%d)",
            ctypes.windll.kernel32.GetLastError(),
        )
    return ok


def make_click_through(window) -> bool:
    """Pass mouse input straight through, and keep the window out of Alt-Tab."""
    if not is_windows():
        return False
    handle = _hwnd(window)
    if handle is None:
        return False
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.restype = ctypes.c_long
    current = user32.GetWindowLongW(wintypes.HWND(handle), _GWL_EXSTYLE)
    wanted = (
        current
        | _WS_EX_TRANSPARENT
        | _WS_EX_LAYERED
        | _WS_EX_NOACTIVATE
        | _WS_EX_TOOLWINDOW
    )
    if wanted != current:
        user32.SetWindowLongW(wintypes.HWND(handle), _GWL_EXSTYLE, wanted)
    return True


def raise_topmost(window) -> bool:
    """Above everything, including full-screen windows, without taking focus."""
    if not is_windows():
        return False
    handle = _hwnd(window)
    if handle is None:
        return False
    return bool(
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(handle),
            wintypes.HWND(_HWND_TOPMOST),
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
        )
    )


def windows_build() -> int:
    """The current build number, or 0 when it cannot be read."""
    if not is_windows():
        return 0
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


def supports_capture_exclusion() -> bool:
    return windows_build() >= MIN_BUILD_FOR_CAPTURE_EXCLUSION


# --- The pointer ---------------------------------------------------------

_SPI_SETCURSORS = 0x0057

#: Every system cursor ``SetSystemCursor`` can replace. Applications that ask
#: for a stock cursor get these shared ones, so blanking them hides the pointer
#: almost everywhere; only an application drawing a cursor of its own keeps it.
_SYSTEM_CURSORS = (
    32512,  # OCR_NORMAL
    32513,  # OCR_IBEAM
    32514,  # OCR_WAIT
    32515,  # OCR_CROSS
    32516,  # OCR_UP
    32642,  # OCR_SIZENWSE
    32643,  # OCR_SIZENESW
    32644,  # OCR_SIZEWE
    32645,  # OCR_SIZENS
    32646,  # OCR_SIZEALL
    32648,  # OCR_NO
    32649,  # OCR_HAND
    32650,  # OCR_APPSTARTING
    32651,  # OCR_HELP
)


def _user32():
    """user32 with the handle-sized signatures 64-bit Python needs."""
    user32 = ctypes.windll.user32
    user32.CreateCursor.restype = wintypes.HANDLE
    user32.CreateCursor.argtypes = [
        wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    return user32


def hide_pointer() -> bool:
    """Swap every system cursor for a blank one until :func:`restore_pointer`.

    ``ShowCursor`` only counts for the calling thread's own windows, and this
    window takes no input, so the system cursors themselves are what has to
    change. Each ``SetSystemCursor`` takes ownership of the cursor it is given,
    so every one gets a fresh blank.
    """
    if not is_windows():
        return False
    user32 = _user32()
    width = user32.GetSystemMetrics(13)  # SM_CXCURSOR
    height = user32.GetSystemMetrics(14)  # SM_CYCURSOR
    stride = (width + 15) // 16 * 2
    and_mask = ctypes.create_string_buffer(b"\xff" * (stride * height))
    xor_mask = ctypes.create_string_buffer(stride * height)
    instance = ctypes.windll.kernel32.GetModuleHandleW(None)
    hidden = False
    for cursor_id in _SYSTEM_CURSORS:
        blank = user32.CreateCursor(instance, 0, 0, width, height, and_mask, xor_mask)
        if blank and user32.SetSystemCursor(blank, cursor_id):
            hidden = True
    if not hidden:
        log.warning("could not hide the pointer (%d)", ctypes.windll.kernel32.GetLastError())
    return hidden


def pointer_position() -> tuple[int, int] | None:
    """Where the pointer is on the virtual screen, or ``None`` when unknown."""
    if not is_windows():
        return None
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return (point.x, point.y)


def restore_pointer() -> None:
    """Reload the user's cursor scheme, undoing :func:`hide_pointer`."""
    if not is_windows():
        return
    ctypes.windll.user32.SystemParametersInfoW(_SPI_SETCURSORS, 0, None, 0)


# --- Shell flyouts -------------------------------------------------------

#: Processes that host only the shell's light-dismiss surfaces: Start, search,
#: the notification centre and quick settings. They sit in a z-order band above
#: every topmost window, so the overlay cannot cover them.
_SHELL_FLYOUT_HOSTS = frozenset(
    {
        "startmenuexperiencehost.exe",
        "searchhost.exe",
        "searchapp.exe",
        "searchui.exe",
        "shellexperiencehost.exe",
        "shellhost.exe",
    }
)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_VK_ESCAPE = 0x1B


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    # The mouse member is the largest; it sets the size SendInput checks.
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _foreground_process_name() -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    window = user32.GetForegroundWindow()
    if not window:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    kernel32.OpenProcess.restype = wintypes.HANDLE
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        length = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(
            wintypes.HANDLE(process), 0, buffer, ctypes.byref(length)
        ):
            return ""
        return buffer.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(process))


def close_shell_flyouts() -> bool:
    """Close Start, search or a notification flyout if one has the focus.

    Being above them would need ``uiAccess``, which only a signed executable
    installed under Program Files gets. Escape is what closes them by hand, and
    it is only sent when one of them is in front, so it can never reach an
    application. Returns True when it sent one.
    """
    if not is_windows():
        return False
    if _foreground_process_name() not in _SHELL_FLYOUT_HOSTS:
        return False
    down = _INPUT(type=_INPUT_KEYBOARD)
    down.u.ki = _KEYBDINPUT(wVk=_VK_ESCAPE)
    up = _INPUT(type=_INPUT_KEYBOARD)
    up.u.ki = _KEYBDINPUT(wVk=_VK_ESCAPE, dwFlags=_KEYEVENTF_KEYUP)
    inputs = (_INPUT * 2)(down, up)
    sent = ctypes.windll.user32.SendInput(2, inputs, ctypes.sizeof(_INPUT))
    log.info("closed a shell flyout that sat above the overlay")
    return sent == 2
