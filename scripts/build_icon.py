"""Render the Escort logo into packaging/escort.ico and tray PNGs.

    python scripts/build_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from escort.desktop.logo import render_logo  # noqa: E402

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    out = ROOT / "packaging"
    out.mkdir(exist_ok=True)
    frames = [render_logo(size) for size in ICO_SIZES]
    ico = out / "escort.ico"
    frames[-1].save(ico, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=frames[:-1])
    render_logo(64).save(out / "tray.png")
    render_logo(64, live=True).save(out / "tray-live.png")
    print(f"wrote {ico}, tray.png, tray-live.png")


if __name__ == "__main__":
    main()
