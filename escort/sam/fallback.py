from __future__ import annotations

from PIL import Image


def share_rect(width: int, height: int, region: str) -> tuple[int, int, int, int]:
    left = int(width * 0.08)
    top = int(height * 0.12)
    right = int(width * 0.96)
    bottom = int(height * 0.94)
    share_w = max(1, right - left)
    share_h = max(1, bottom - top)
    if region == "top":
        bottom = top + max(1, int(share_h * 0.38))
    elif region == "bottom":
        top = top + int(share_h * 0.62)
    elif region == "left":
        right = left + max(1, int(share_w * 0.5))
    elif region == "right":
        left = left + int(share_w * 0.5)
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return left, top, right, bottom


def crop_share(image: Image.Image, region: str) -> Image.Image:
    left, top, right, bottom = share_rect(image.width, image.height, region)
    return image.crop((left, top, right, bottom))
