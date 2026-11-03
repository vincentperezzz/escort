"""The title and process name of the window the user is looking at (Windows only, fails soft).

A cheap local signal for the proactive engine: "escort - Cursor" or "Sales CRM - Google Meet"
says which app and project are on screen without a vision call. Returns None elsewhere.
"""

from __future__ import annotations

import os
import sys


def foreground_window() -> dict[str, str] | None:
    """{"title": window title, "process": exe name} of the foreground window, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = ""
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                size = wintypes.DWORD(260)
                path = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    process = os.path.basename(path.value)
            finally:
                kernel32.CloseHandle(handle)
        return {"title": buffer.value, "process": process}
    except Exception:
        return None
