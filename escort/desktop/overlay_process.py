"""The Electron overlay (overlay/ in the repo) as a child process of the desktop app.

EscortOverlay.exe follows GET /api/overlay by itself and registers Ctrl+Shift+E,
so the backend only starts it, keeps it alive (a few restarts at most) and
kills it on exit. It also quits on its own when --parent-pid dies or the backend
is unreachable for ~10s. Launch contract: the '=' form of both arguments;
Electron 45 on Windows exits with -1 before main.js runs otherwise.
"""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from escort.paths import repo_root

log = logging.getLogger("escort.desktop")

EXE_NAME = "EscortOverlay.exe"
DEV_RELATIVE = Path("overlay") / "out" / "EscortOverlay-win32-x64" / EXE_NAME
MAX_RESTARTS = 3
STARTUP_GRACE_S = 1.0
WATCH_INTERVAL_S = 1.0
RESTART_DELAY_S = 1.0
CREATE_NO_WINDOW = 0x08000000
# ESCORT_OVERLAY=pywebview forces the old in-process overlay (debugging, or a broken Electron build).
DISABLE_ENV = "ESCORT_OVERLAY"


def find_overlay_exe(
    frozen: bool | None = None,
    executable: str | None = None,
    root: Path | None = None,
) -> Path | None:
    """Installed: <dir of Escort.exe>\\overlay\\EscortOverlay.exe. Source: overlay/out/... in the repo."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        candidate = Path(executable or sys.executable).resolve().parent / "overlay" / EXE_NAME
    else:
        candidate = (root or repo_root()) / DEV_RELATIVE
    return candidate if candidate.is_file() else None


def overlay_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"pywebview", "0", "off", "false"}


def _kill_tree(pid: int) -> None:
    """Kill the Electron main process and its GPU/renderer children."""
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        timeout=10,
        check=False,
    )


class ElectronOverlay:
    """Starts, watches and stops EscortOverlay.exe. All methods are thread-safe and never raise."""

    def __init__(
        self,
        exe: Path,
        url: str,
        parent_pid: int | None = None,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        kill_tree: Callable[[int], None] | None = None,
        max_restarts: int = MAX_RESTARTS,
        startup_grace: float = STARTUP_GRACE_S,
        watch_interval: float = WATCH_INTERVAL_S,
        restart_delay: float = RESTART_DELAY_S,
        on_gone: Callable[[], None] | None = None,
    ) -> None:
        self.exe = Path(exe)
        self.url = url.rstrip("/")
        self.parent_pid = os.getpid() if parent_pid is None else parent_pid
        self._popen = popen
        self._kill_tree = kill_tree or (_kill_tree if os.name == "nt" else None)
        self.max_restarts = max_restarts
        self.startup_grace = startup_grace
        self.watch_interval = watch_interval
        self.restart_delay = restart_delay
        self.on_gone = on_gone
        self.restarts = 0
        self.proc: Any = None
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._watcher: threading.Thread | None = None
        self._atexit = False

    def command(self) -> list[str]:
        return [str(self.exe), f"--url={self.url}", f"--parent-pid={self.parent_pid}"]

    def _spawn(self) -> Any:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(self.exe.parent),
        }
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        proc = self._popen(self.command(), **kwargs)
        log.info("Electron overlay started (pid %s): %s", getattr(proc, "pid", "?"), " ".join(self.command()))
        return proc

    def start(self) -> bool:
        """Spawn the overlay; False if it cannot start or exits within the startup grace period."""
        if self._stopping.is_set():
            return False
        try:
            proc = self._spawn()
        except Exception as exc:
            log.warning("Electron overlay did not start (%s): %s", self.exe, exc)
            return False
        try:
            code = proc.wait(timeout=self.startup_grace)
        except subprocess.TimeoutExpired:
            code = None
        except Exception as exc:
            log.warning("Electron overlay startup check failed: %s", exc)
            code = proc.poll()
        if code is not None:
            log.warning("Electron overlay exited at startup with code %s.", code)
            return False
        with self._lock:
            self.proc = proc
        if not self._atexit:
            atexit.register(self.stop)
            self._atexit = True
        self._watcher = threading.Thread(target=self._watch, name="escort-overlay-watch", daemon=True)
        self._watcher.start()
        return True

    def is_running(self) -> bool:
        with self._lock:
            proc = self.proc
        if proc is None or self._stopping.is_set():
            return False
        try:
            return proc.poll() is None
        except Exception:
            return False

    def _watch(self) -> None:
        while not self._stopping.wait(self.watch_interval):
            with self._lock:
                proc = self.proc
            if proc is None:
                return
            code = proc.poll()
            if code is None:
                continue
            if self.restarts >= self.max_restarts:
                log.warning("Electron overlay exited (code %s); gave up after %d restarts.", code, self.restarts)
                with self._lock:
                    self.proc = None
                self._gone()
                return
            self.restarts += 1
            log.warning("Electron overlay exited (code %s); restart %d of %d.", code, self.restarts, self.max_restarts)
            if self._stopping.wait(self.restart_delay):
                return
            try:
                new = self._spawn()
            except Exception as exc:
                log.warning("Electron overlay restart failed: %s", exc)
                continue  # the old, exited proc stays; the next tick counts another attempt
            with self._lock:
                if self._stopping.is_set():
                    self._terminate(new)
                    return
                self.proc = new

    def _gone(self) -> None:
        if self.on_gone is None:
            return
        try:
            self.on_gone()
        except Exception as exc:
            log.warning("Overlay gone callback failed: %s", exc)

    def _terminate(self, proc: Any) -> None:
        try:
            if proc.poll() is not None:
                return
        except Exception:
            pass
        try:
            if self._kill_tree is not None:
                self._kill_tree(proc.pid)
            else:
                proc.terminate()
            proc.wait(timeout=5)
        except Exception as exc:
            log.warning("Electron overlay did not stop cleanly: %s", exc)
            try:
                proc.kill()
            except Exception:
                pass

    def stop(self) -> None:
        """App exit: no more restarts; kill the overlay and its children. Idempotent."""
        self._stopping.set()
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is not None:
            self._terminate(proc)
            log.info("Electron overlay stopped.")
        if self._atexit:
            try:
                atexit.unregister(self.stop)
            except Exception:
                pass
            self._atexit = False


def start_electron_overlay(
    url: str,
    parent_pid: int | None = None,
    on_gone: Callable[[], None] | None = None,
    **kwargs: Any,
) -> ElectronOverlay | None:
    """Find and start EscortOverlay.exe. None (logged) when disabled, missing or it fails to start."""
    if overlay_disabled():
        log.info("Electron overlay disabled by %s; using the pywebview overlay.", DISABLE_ENV)
        return None
    exe = find_overlay_exe()
    if exe is None:
        log.info("Electron overlay not found; using the pywebview overlay.")
        return None
    overlay = ElectronOverlay(exe, url, parent_pid, on_gone=on_gone, **kwargs)
    if not overlay.start():
        overlay.stop()
        log.warning("Falling back to the pywebview overlay.")
        return None
    return overlay
