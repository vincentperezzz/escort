"""Frame change detection that behaves like a video encoder's keyframe logic.

`FrameGate` decides which screen grabs are worth sending to the vision model:

- Repeated frames are never resent: every grab is compared with the last *kept* frame.
- Mouse movement does not count: a change must touch enough pixels and enough tiles,
  and a caller that knows the cursor position can mask it with `ignore_box`.
- Scrolls, fades and animations are debounced: a change is held as a pending
  candidate and kept only once the next grab is near-identical (the screen settled).
- A screen that never settles (video, live animation) is rate-limited: at most one
  frame per `max_motion_interval_s`, always the newest grab.

All of it is pure PIL on a small blurred greyscale copy, so a 1080p grab costs a few ms.
"""

from __future__ import annotations

import time
from typing import Callable

from PIL import Image, ImageChops, ImageFilter

Box = tuple[int, int, int, int]


def dhash(image: Image.Image, hash_size: int = 8) -> int:
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.get_flattened_data())
    bits = 0
    row_width = hash_size + 1
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * row_width + col]
            right = pixels[row * row_width + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def mean_abs_diff(left: Image.Image, right: Image.Image) -> float:
    a = list(left.convert("L").resize((32, 32), Image.Resampling.BOX).get_flattened_data())
    b = list(right.convert("L").resize((32, 32), Image.Resampling.BOX).get_flattened_data())
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


class _Frame:
    """A grab reduced to the working grid, plus what the caller needs back."""

    __slots__ = ("work", "token", "source_size")

    def __init__(self, work: Image.Image, token: str, source_size: tuple[int, int]) -> None:
        self.work = work
        self.token = token
        self.source_size = source_size


class _Delta:
    __slots__ = ("pixel_fraction", "tile_fraction", "changed_tiles", "bbox")

    def __init__(self, pixel_fraction: float, tile_fraction: float, changed_tiles: int, bbox: Box | None) -> None:
        self.pixel_fraction = pixel_fraction
        self.tile_fraction = tile_fraction
        self.changed_tiles = changed_tiles
        self.bbox = bbox


class FrameGate:
    """Keep a grab only when it is a settled, meaningful change from the last kept grab.

    Each grab is shrunk to `grid` (default 384x216), greyed and box-blurred, which wipes
    out compression noise and caret blink. Against the reference (last kept frame):

    - a pixel is *changed* when its grey level moved by >= `pixel_threshold`;
    - the grid is split into `tiles` (default 32x18, 12x12 px each) and a tile is
      *changed* when >= `tile_min_share` of its pixels changed;
    - the change is *significant* when the changed-pixel share is >= `min_pixel_fraction`
      AND at least `min_changed_tiles` tiles changed. A moving cursor (old spot + new
      spot) stays well under both, a new line of text or a slide change does not.

    A significant change becomes a pending candidate. It is kept ("settled") when the
    next grab does not differ significantly from it; if the screen keeps changing, the
    newest grab is kept ("motion") once the change has lasted `max_motion_interval_s`.
    If the screen goes back to the reference, the candidate is dropped.

    `hash_threshold` and `mae_threshold` are accepted for backward compatibility with
    the old dHash/mean-drift gate and are not used.
    """

    def __init__(
        self,
        hash_threshold: int = 6,
        mae_threshold: float = 8.0,
        *,
        grid: tuple[int, int] = (384, 216),
        tiles: tuple[int, int] = (32, 18),
        pixel_threshold: int = 24,
        tile_min_share: float = 0.04,
        min_pixel_fraction: float = 0.002,
        min_changed_tiles: int = 4,
        scene_cut_fraction: float = 0.35,
        max_motion_interval_s: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hash_threshold = hash_threshold
        self.mae_threshold = mae_threshold
        self.grid = grid
        self.tiles = tiles
        self.pixel_threshold = pixel_threshold
        self.tile_min_share = tile_min_share
        self.min_pixel_fraction = min_pixel_fraction
        self.min_changed_tiles = min_changed_tiles
        self.scene_cut_fraction = scene_cut_fraction
        self.max_motion_interval_s = max_motion_interval_s
        self.clock = clock
        self._mask_lut = [255 if value >= pixel_threshold else 0 for value in range(256)]
        tile_cut = round(255 * tile_min_share)
        self._tile_lut = [255 if value >= max(1, tile_cut) else 0 for value in range(256)]
        self._reference: _Frame | None = None
        self._pending: _Frame | None = None
        self._pending_since: float = 0.0
        self.last_change: dict | None = None

    @property
    def scene_cut(self) -> bool:
        """True when the most recent kept change replaced most of the screen (new slide/app)."""
        return bool(self.last_change) and self.last_change["fraction"] >= self.scene_cut_fraction

    def reset(self) -> None:
        """Forget the baseline, so the next frame counts as changed (e.g. after switching display)."""
        self._reference = None
        self._pending = None
        self._pending_since = 0.0
        self.last_change = None

    def changed(self, image: Image.Image, ignore_box: Box | None = None) -> tuple[bool, str]:
        """Return (keep, token) for one grab; `token` is the frame's 16-hex dHash.

        `ignore_box` is an optional (x1, y1, x2, y2) region in source pixels, e.g. the
        cursor, whose pixels never count as changed.
        """
        frame = self._prepare(image)
        reference = self._reference
        if reference is None:
            return self._keep(frame, None, "first"), frame.token

        delta = self._delta(reference, frame, ignore_box)
        if not self._significant(delta):
            self._pending = None
            return False, frame.token

        now = self.clock()
        pending = self._pending
        if pending is not None and not self._significant(self._delta(pending, frame, ignore_box)):
            return self._keep(frame, delta, "settled"), frame.token
        if pending is None:
            self._pending_since = now
        elif now - self._pending_since >= self.max_motion_interval_s:
            return self._keep(frame, delta, "motion"), frame.token
        self._pending = frame
        return False, frame.token

    def _prepare(self, image: Image.Image) -> _Frame:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        width, height = self.grid
        factor = min(image.width // width, image.height // height)
        small = image.reduce(factor) if factor > 1 else image  # integer box reduce: ~2x faster than resize
        if small.size != self.grid:
            small = small.resize(self.grid, Image.Resampling.BOX)
        work = small.convert("L").filter(ImageFilter.BoxBlur(1))
        return _Frame(work, f"{dhash(work):016x}", image.size)

    def _delta(self, before: _Frame, after: _Frame, ignore_box: Box | None) -> _Delta:
        diff = ImageChops.difference(before.work, after.work)
        if ignore_box is not None:
            masked = self._to_grid(ignore_box, after.source_size)
            if masked is not None:
                diff.paste(0, masked)
        mask = diff.point(self._mask_lut)
        width, height = self.grid
        pixel_fraction = mask.histogram()[255] / (width * height)
        tile_mask = mask.resize(self.tiles, Image.Resampling.BOX).point(self._tile_lut)
        tiles_x, tiles_y = self.tiles
        changed_tiles = tile_mask.histogram()[255]
        bbox = None
        tile_box = tile_mask.getbbox() if changed_tiles else None
        if tile_box is not None:
            source_w, source_h = after.source_size
            x1, y1, x2, y2 = tile_box
            bbox = (
                x1 * source_w // tiles_x,
                y1 * source_h // tiles_y,
                min(source_w, -(-x2 * source_w // tiles_x)),
                min(source_h, -(-y2 * source_h // tiles_y)),
            )
        return _Delta(pixel_fraction, changed_tiles / (tiles_x * tiles_y), changed_tiles, bbox)

    def _significant(self, delta: _Delta) -> bool:
        return delta.pixel_fraction >= self.min_pixel_fraction and delta.changed_tiles >= self.min_changed_tiles

    def _to_grid(self, box: Box, source_size: tuple[int, int]) -> Box | None:
        source_w, source_h = source_size
        width, height = self.grid
        x1 = max(0, box[0] * width // source_w - 1)
        y1 = max(0, box[1] * height // source_h - 1)
        x2 = min(width, -(-box[2] * width // source_w) + 1)
        y2 = min(height, -(-box[3] * height // source_h) + 1)
        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)

    def _keep(self, frame: _Frame, delta: _Delta | None, kind: str) -> bool:
        self._reference = frame
        self._pending = None
        self._pending_since = 0.0
        if delta is None:
            self.last_change = {"fraction": 1.0, "bbox": (0, 0, *frame.source_size), "kind": kind}
        else:
            self.last_change = {"fraction": delta.tile_fraction, "bbox": delta.bbox, "kind": kind}
        return True
