"""Escort logo art, drawn with PIL so the tray needs no bundled image files.

Geometry is defined on a 64x64 canvas: a dark squircle tile with a faint inner
hairline, three round-capped bars and a status dot (violet idle, red live).
"""

from __future__ import annotations

from PIL import Image, ImageDraw

TILE = "#0b0c12"
HAIRLINE = (255, 255, 255, round(0.12 * 255))
BAR = "#f4f5fa"
DOT_IDLE = "#8b7cff"
DOT_LIVE = "#ff6a55"

_CANVAS = 64
_RADIUS = 18
_BAR_WIDTH = 5.5
_BARS = ((21, 19, 45), (32, 19, 36), (43, 19, 45))  # (y, x_start, x_end)
_DOT = (47, 32, 4.6)  # (cx, cy, r)
_SUPERSAMPLE = 4


def render_logo(size: int = 64, live: bool = False) -> Image.Image:
    """Return an RGBA logo of size x size pixels, drawn at 4x and downsampled."""
    big = size * _SUPERSAMPLE
    s = big / _CANVAS
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, big - 1, big - 1), radius=_RADIUS * s, fill=TILE)

    # Translucent hairline goes on its own layer: drawing RGBA ink straight onto an
    # RGBA image replaces the alpha instead of blending over the tile.
    hair = max(1, round(s))  # 1 logical px
    line = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(line).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=_RADIUS * s, outline=HAIRLINE, width=hair
    )
    image.alpha_composite(line)

    draw = ImageDraw.Draw(image)

    half = _BAR_WIDTH * s / 2
    for y, x0, x1 in _BARS:
        cy, a, b = y * s, x0 * s, x1 * s
        draw.rectangle((a, cy - half, b, cy + half), fill=BAR)
        draw.ellipse((a - half, cy - half, a + half, cy + half), fill=BAR)
        draw.ellipse((b - half, cy - half, b + half, cy + half), fill=BAR)

    cx, cy, r = (value * s for value in _DOT)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=DOT_LIVE if live else DOT_IDLE)

    return image.resize((size, size), Image.LANCZOS)
