"""System-wide hotkey via RegisterHotKey, served by its own message-loop thread."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any, Callable

log = logging.getLogger("escort.desktop.hotkey")

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
_HOTKEY_ID = 0xE5C0


def _default_user32() -> Any:
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    dll.RegisterHotKey.restype = wintypes.BOOL
    dll.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    dll.UnregisterHotKey.restype = wintypes.BOOL
    dll.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    dll.GetMessageW.restype = wintypes.BOOL
    dll.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    dll.PostThreadMessageW.restype = wintypes.BOOL
    return dll


class GlobalHotkey:
    """Ctrl+Shift+E by default. start() returns False (never raises) if unavailable."""

    def __init__(
        self,
        callback: Callable[[], Any],
        modifiers: int = MOD_CONTROL | MOD_SHIFT,
        key: str = "E",
        user32: Any = None,
        platform: str | None = None,
    ) -> None:
        self.callback = callback
        self.modifiers = modifiers | MOD_NOREPEAT
        self.vk = ord(key.upper())
        self._user32 = user32
        self._platform = platform or sys.platform
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self.registered = False

    def start(self, timeout: float = 2.0) -> bool:
        if self._platform != "win32":
            log.warning("Global hotkey is only available on Windows")
            return False
        try:
            if self._user32 is None:
                self._user32 = _default_user32()
            self._thread = threading.Thread(target=self._run, name="escort-hotkey", daemon=True)
            self._thread.start()
        except Exception as exc:
            log.warning("Global hotkey did not start: %s", exc)
            return False
        self._ready.wait(timeout)
        return self.registered

    def _run(self) -> None:
        user32 = self._user32
        try:
            self._thread_id = threading.get_native_id()
            if not user32.RegisterHotKey(None, _HOTKEY_ID, self.modifiers, self.vk):
                log.warning("Ctrl+Shift+E is taken by another app; overlay hotkey disabled")
                return
            self.registered = True
        except Exception as exc:
            log.warning("RegisterHotKey failed: %s", exc)
            return
        finally:
            self._ready.set()
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception as exc:
                        log.warning("Hotkey handler failed: %s", exc)
        except Exception as exc:
            log.warning("Hotkey loop stopped: %s", exc)
        finally:
            try:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass
            self.registered = False

    def stop(self) -> None:
        if not self._thread or not self._thread.is_alive() or self._thread_id is None:
            return
        try:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception as exc:
            log.warning("Could not stop hotkey thread: %s", exc)
            return
        self._thread.join(timeout=1.0)
