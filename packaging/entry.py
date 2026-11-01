"""Frozen Windows entry.

Must live outside escort/desktop. PyInstaller puts the script directory on
sys.path, so a sibling named secrets.py would shadow the stdlib module that
Starlette imports (token_hex) and crash Escort.exe on launch.
"""

import os
import sys

# Crash logging first, before pythonnet / pywebview / PortAudio load: a native
# crash then still leaves every thread's Python stack in %APPDATA%\Escort\logs.
# It also points the missing stdout/stderr of the windowed build at that log.
try:
    from escort.desktop import crashlog

    crashlog.install()
except Exception:
    pass

# Windowed (console=False) builds have no stdout/stderr; uvicorn's logging calls
# .isatty() on them and crashes at startup. Only reached if crash logging failed.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

from escort.desktop.launch import main

if __name__ == "__main__":
    main()
