from __future__ import annotations

import logging
import os
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("escort.desktop.instance")

IPC_HOST = "127.0.0.1"
IPC_PORT = 8759
MUTEX_NAME = "Local\\EscortDesktopSingleInstance_v1"


def escort_protocol_urls(argv: list[str]) -> list[str]:
    return [arg for arg in argv if arg.startswith("escort://")]


def register_escort_protocol() -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
    except ImportError:
        return
    if getattr(sys, "frozen", False):
        command = f'"{Path(sys.executable).resolve()}" "%1"'
    else:
        command = f'"{Path(sys.executable).resolve()}" -m escort.desktop "%1"'
    try:
        classes = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\escort")
        winreg.SetValueEx(classes, "", 0, winreg.REG_SZ, "URL:Escort Protocol")
        winreg.SetValueEx(classes, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.CloseKey(classes)
        shell = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\escort\shell\open\command")
        winreg.SetValueEx(shell, "", 0, winreg.REG_SZ, command)
        winreg.CloseKey(shell)
    except OSError as exc:
        log.warning("Could not register escort:// protocol handler: %s", exc)


class SingleInstance:
    def __init__(self, port: int | None = None) -> None:
        self.port = int(os.environ.get("ESCORT_IPC_PORT") or (port if port is not None else IPC_PORT))
        self._mutex_handle = None
        self._listen_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def try_acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
            exists = kernel32.GetLastError() == 183
            if exists:
                if handle:
                    kernel32.CloseHandle(handle)
                return False
            self._mutex_handle = handle
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((IPC_HOST, self.port))
            sock.listen(8)
            self._listen_sock = sock
            return True
        except OSError:
            if self._mutex_handle is not None and sys.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
            return False

    def forward(self, payload: str) -> bool:
        data = (payload.strip() + "\n").encode("utf-8")
        try:
            with socket.create_connection((IPC_HOST, self.port), timeout=3.0) as conn:
                conn.sendall(data)
            return True
        except OSError as exc:
            log.debug("Could not forward to running Escort: %s", exc)
            return False

    def start_listener(self, handler: Callable[[str], None]) -> None:
        sock = self._listen_sock
        if sock is None:
            return

        def _serve() -> None:
            while not self._stop.is_set():
                try:
                    sock.settimeout(0.5)
                    client, _addr = sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    with client:
                        raw = client.recv(65536)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        try:
                            handler(line)
                        except Exception as exc:
                            log.warning("Protocol handler failed: %s", exc)
                except OSError:
                    continue

        self._thread = threading.Thread(target=_serve, name="escort-ipc", daemon=True)
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except OSError:
                pass
            self._listen_sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._mutex_handle is not None and sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None
