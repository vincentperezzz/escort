from __future__ import annotations

import re

from PIL import Image

from taglish_listener.models import Box


_BOX = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>"
)


def parse_boxes(text: str) -> list[Box]:
    boxes: list[Box] = []
    for match in _BOX.finditer(text):
        score = float(match.group(7)) if match.group(7) else None
        boxes.append(
            Box(
                x1=int(match.group(1)),
                y1=int(match.group(2)),
                x2=int(match.group(3)),
                y2=int(match.group(4)),
                w=int(match.group(5)),
                h=int(match.group(6)),
                score=score,
            )
        )
    return boxes


def extract_text(node: object) -> str:
    chunks: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"text", "output_text"} and isinstance(item, str):
                    chunks.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and "<|box;" in value:
            chunks.append(value)

    walk(node)
    return "\n".join(chunks)


def pick_box(boxes: list[Box], region: str) -> Box | None:
    if not boxes:
        return None

    def fits(box: Box) -> bool:
        if box.w <= 0 or box.h <= 0:
            return False
        nx = box.cx / box.w
        ny = box.cy / box.h
        if region == "top":
            return ny <= 0.45
        if region == "bottom":
            return ny >= 0.55
        if region == "left":
            return nx <= 0.5
        if region == "right":
            return nx >= 0.5
        return True

    ranked = [box for box in boxes if fits(box)] or list(boxes)
    if region == "top":
        ranked.sort(key=lambda box: box.cy)
    elif region == "bottom":
        ranked.sort(key=lambda box: -box.cy)
    elif region == "left":
        ranked.sort(key=lambda box: box.cx)
    elif region == "right":
        ranked.sort(key=lambda box: -box.cx)
    else:
        ranked.sort(key=lambda box: -((box.x2 - box.x1) * (box.y2 - box.y1)))
    return ranked[0]


def scale_box(box: Box, image_w: int, image_h: int) -> Box:
    if box.w == image_w and box.h == image_h:
        return box
    sx = image_w / box.w if box.w else 1
    sy = image_h / box.h if box.h else 1
    return Box(
        x1=int(box.x1 * sx),
        y1=int(box.y1 * sy),
        x2=int(box.x2 * sx),
        y2=int(box.y2 * sy),
        w=image_w,
        h=image_h,
        score=box.score,
    )


def crop_box(image: Image.Image, box: Box) -> Image.Image:
    scaled = scale_box(box, image.width, image.height)
    left = max(0, min(image.width - 1, scaled.x1))
    top = max(0, min(image.height - 1, scaled.y1))
    right = max(left + 1, min(image.width, scaled.x2 + 1))
    bottom = max(top + 1, min(image.height, scaled.y2 + 1))
    return image.crop((left, top, right, bottom))
