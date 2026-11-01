"""Win32 helpers for the in-meeting overlay window (ctypes, no extra deps).

Every function is a no-op that returns False off Windows or when a call fails,
so callers never need to guard against platform differences.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

log = logging.getLogger("escort.desktop.overlay")

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
# WDA_EXCLUDEFROMCAPTURE only exists from Windows 10 2004 (build 19041). Older
# builds treat 0x11 like WDA_MONITOR and paint a black box instead, so we skip it.
MIN_CAPTURE_EXCLUSION_BUILD = 19041

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SPI_GETWORKAREA = 0x0030

_USER32: Any = None


def is_windows() -> bool:
    return sys.platform == "win32"


def windows_build() -> int:
    if not is_windows():
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def capture_exclusion_supported() -> bool:
    return is_windows() and windows_build() >= MIN_CAPTURE_EXCLUSION_BUILD


def _user32() -> Any:
    """A private user32 handle so our argtypes never leak into pywebview's calls."""
    global _USER32
    if _USER32 is None:
        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
        dll.SetWindowDisplayAffinity.restype = wintypes.BOOL
        dll.GetWindowDisplayAffinity.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        dll.GetWindowDisplayAffinity.restype = wintypes.BOOL
        dll.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        dll.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        dll.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        dll.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        dll.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        dll.ShowWindow.restype = wintypes.BOOL
        dll.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
        )
        dll.SetWindowPos.restype = wintypes.BOOL
        dll.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        dll.GetWindowRect.restype = wintypes.BOOL
        dll.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
        dll.SystemParametersInfoW.restype = wintypes.BOOL
        dll.GetDpiForWindow.argtypes = (wintypes.HWND,)
        dll.GetDpiForWindow.restype = wintypes.UINT
        _USER32 = dll
    return _USER32


def set_capture_excluded(hwnd: int | None, excluded: bool) -> bool:
    """Hide (or re-show) a window from screen capture. True if the call succeeded.

    Unsupported builds return False without touching the window.
    """
    if not hwnd or not capture_exclusion_supported():
        return False
    try:
        ok = bool(_user32().SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE))
    except Exception as exc:
        log.warning("SetWindowDisplayAffinity failed: %s", exc)
        return False
    if not ok:
        log.warning("SetWindowDisplayAffinity returned an error (%s)", ctypes.get_last_error())
    return ok


def get_display_affinity(hwnd: int | None) -> int | None:
    if not hwnd or not is_windows():
        return None
    value = wintypes.DWORD()
    try:
        if _user32().GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
            return int(value.value)
    except Exception:
        pass
    return None


def make_tool_window(hwnd: int | None) -> bool:
    """Keep the window out of Alt-Tab and the taskbar and stop it taking focus."""
    if not hwnd or not is_windows():
        return False
    try:
        user32 = _user32()
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        return True
    except Exception as exc:
        log.warning("Could not set overlay window styles: %s", exc)
        return False


def show_no_activate(hwnd: int | None, place_top_center: bool = False, margin: int = 24) -> bool:
    """Show the window topmost without stealing focus, optionally moving it to top-center."""
    if not hwnd or not is_windows():
        return False
    try:
        user32 = _user32()
        flags = SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        x = y = 0
        if place_top_center:
            area, rect = wintypes.RECT(), wintypes.RECT()
            user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
            width = rect.right - rect.left
            x = area.left + max(0, (area.right - area.left - width) // 2)
            y = area.top + int(margin * scale)
        else:
            flags |= SWP_NOMOVE
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, flags)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        return True
    except Exception as exc:
        log.warning("Could not show overlay: %s", exc)
        return False


def hide(hwnd: int | None) -> bool:
    if not hwnd or not is_windows():
        return False
    try:
        _user32().ShowWindow(hwnd, SW_HIDE)
        return True
    except Exception as exc:
        log.warning("Could not hide overlay: %s", exc)
        return False
