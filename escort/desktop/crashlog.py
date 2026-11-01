"""Crash visibility for the desktop app: faulthandler, uncaught-exception hooks and a log file.

install() must run before anything native is imported (pythonnet, pywebview,
PortAudio), so a hard crash such as an access violation still leaves a Python
traceback of every thread in %APPDATA%\\Escort\\logs\\crash.log. It is safe in the
windowed (console=False) build, where sys.stdout and sys.stderr are None: they
are pointed at the crash log so stray prints and tracebacks land there too.
Only stdlib imports here.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

CRASH_LOG = "crash.log"
APP_LOG = "escort.log"
KEEP_CRASH_LOGS = 5
APP_LOG_BYTES = 2_000_000
APP_LOG_BACKUPS = 3

_state: dict[str, Any] = {}


def log_dir() -> Path:
    override = os.environ.get("ESCORT_LOG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    root = Path(base) / "Escort" if base else Path.home() / ".config" / "Escort"
    return root / "logs"


def _rotate(folder: Path, name: str, keep: int) -> None:
    """crash.log -> crash.1.log -> ... -> crash.<keep>.log, so the last few runs survive."""
    stem, suffix = os.path.splitext(name)
    for index in range(keep, 0, -1):
        older = folder / f"{stem}.{index}{suffix}"
        newer = folder / (name if index == 1 else f"{stem}.{index - 1}{suffix}")
        try:
            if newer.exists():
                if older.exists():
                    older.unlink()
                newer.rename(older)
        except OSError:
            pass


def install(folder: Path | None = None) -> Path | None:
    """Enable crash logging once per process. Returns the log folder, or None if it could not be set up."""
    if "dir" in _state:
        return _state["dir"]
    try:
        target = folder or log_dir()
        target.mkdir(parents=True, exist_ok=True)
        _rotate(target, CRASH_LOG, KEEP_CRASH_LOGS)
        # Line-buffered and kept open for the life of the process: faulthandler
        # writes to its file descriptor from inside the crash.
        stream: TextIO = open(target / CRASH_LOG, "w", encoding="utf-8", buffering=1)
    except Exception:
        return None
    _state["dir"] = target
    _state["stream"] = stream
    stream.write(f"Escort started {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} python={sys.version.split()[0]}\n")
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, stream)
    faulthandler.enable(file=stream, all_threads=True)
    _install_logging(target)
    _install_hooks()
    return target


def _install_logging(folder: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(
        folder / APP_LOG, maxBytes=APP_LOG_BYTES, backupCount=APP_LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    # pywebview and uvicorn log through their own loggers; let them reach the file.
    for name in ("pywebview", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.propagate = True
    _state["handler"] = handler


def _install_hooks() -> None:
    log = logging.getLogger("escort.crash")
    previous_excepthook = sys.excepthook

    def excepthook(kind, value, tb) -> None:
        log.critical("Uncaught exception", exc_info=(kind, value, tb))
        try:
            previous_excepthook(kind, value, tb)
        except Exception:
            pass

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        log.critical("Uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def unraisable_hook(unraisable: Any) -> None:
        log.error(
            "Unraisable exception in %r: %s",
            unraisable.object,
            unraisable.err_msg or "",
            exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook

