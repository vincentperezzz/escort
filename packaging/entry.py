"""Frozen Windows entry.

Must live outside escort/desktop. PyInstaller puts the script directory on
sys.path, so a sibling named secrets.py would shadow the stdlib module that
Starlette imports (token_hex) and crash Escort.exe on launch.
"""

from escort.desktop.launch import main

if __name__ == "__main__":
    main()
