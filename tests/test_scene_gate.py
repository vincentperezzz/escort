from __future__ import annotations

import random
import re
import time

from PIL import Image, ImageDraw

from escort.diff import FrameGate

SIZE = (1280, 720)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def text_lines(draw: ImageDraw.ImageDraw, x: int, y: int, lines: int, width: int, seed: int) -> None:
    """Rows of glyph-sized dark blocks: looks like body text to a downscaled diff."""
    rng = random.Random(seed)
    for row in range(lines):
        cx = x
        top = y + row * 30
        while cx < x + width:
            word = rng.randint(3, 9)
            for _ in range(word):
                draw.rectangle((cx, top, cx + 7, top + 14), fill=(30, 30, 40))
                cx += 10
            cx += 12


def slide(seed: int, title_color=(40, 70, 160), paragraph: bool = False) -> Image.Image:
    image = Image.new("RGB", SIZE, (250, 250, 248))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, SIZE[0], 90), fill=title_color)
    text_lines(draw, 80, 140, 6, 900, seed)
    if paragraph:
        text_lines(draw, 80, 420, 4, 700, seed + 100)
    return image


def with_cursor(image: Image.Image, x: int, y: int) -> Image.Image:
    framed = image.copy()
    ImageDraw.Draw(framed).rectangle((x, y, x + 15, y + 15), fill=(0, 0, 0), outline=(255, 255, 255))
    return framed


def noise(seed: int) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes("RGB", (160, 90), bytes(rng.randrange(256) for _ in range(160 * 90 * 3))).resize(
        SIZE, Image.Resampling.NEAREST
    )


def feed(gate: FrameGate, frames) -> list[bool]:
    return [gate.changed(frame)[0] for frame in frames]


def test_identical_frames_are_never_kept():
    gate = FrameGate()
    base = slide(1)
    assert feed(gate, [base] + [base.copy() for _ in range(10)]) == [True] + [False] * 10
    assert gate.last_change["kind"] == "first"


def test_moving_cursor_on_a_static_slide_is_never_kept():
    gate = FrameGate()
    base = slide(1)
    assert gate.changed(base)[0] is True
    path = [(60 + step * 97, 80 + (step * 53) % 600) for step in range(12)]
    assert not any(feed(gate, [with_cursor(base, x, y) for x, y in path]))


def test_ignore_box_masks_a_known_cursor_region():
    gate = FrameGate()
    base = slide(1)
    gate.changed(base)
    blob = base.copy()
    ImageDraw.Draw(blob).rectangle((500, 300, 640, 420), fill=(200, 0, 0))
    box = (500, 300, 641, 421)
    assert gate.changed(blob, ignore_box=box)[0] is False
    assert gate.changed(blob.copy(), ignore_box=box)[0] is False


def test_slide_change_is_kept_once_after_it_settles():
    gate = FrameGate()
    first, second = slide(1), slide(2, title_color=(160, 40, 40))
    kept = feed(gate, [first, first.copy(), second, second.copy(), second.copy(), second.copy()])
    assert kept == [True, False, False, True, False, False]
    assert gate.last_change["kind"] == "settled"


def test_cursor_moving_while_the_new_slide_settles_still_settles():
    gate = FrameGate()
    first, second = slide(1), slide(2, title_color=(160, 40, 40))
    kept = feed(gate, [first, with_cursor(second, 100, 100), with_cursor(second, 700, 500)])
    assert kept == [True, False, True]


def test_change_that_reverts_before_settling_is_dropped():
    gate = FrameGate()
    first, popup = slide(1), slide(1)
    ImageDraw.Draw(popup).rectangle((300, 200, 980, 520), fill=(60, 60, 60))
    assert feed(gate, [first, popup, first.copy(), first.copy()]) == [True, False, False, False]


def test_fade_produces_one_frame_at_the_end():
    gate = FrameGate()
    first, second = slide(1), slide(2, title_color=(20, 120, 60))
    steps = [Image.blend(first, second, alpha / 5) for alpha in range(1, 6)]
    kept = feed(gate, [first, *steps, second.copy(), second.copy()])
    assert kept[0] is True
    assert sum(kept[1:]) == 1
    assert kept.index(True, 1) >= 5


def test_scroll_produces_one_frame_at_the_end():
    gate = FrameGate()
    page = Image.new("RGB", (SIZE[0], SIZE[1] * 2), (250, 250, 248))
    text_lines(ImageDraw.Draw(page), 80, 20, 45, 1000, seed=7)
    views = [page.crop((0, offset, SIZE[0], offset + SIZE[1])) for offset in (0, 60, 120, 180, 240, 300)]
    kept = feed(gate, [*views, views[-1].copy()])
    assert kept == [True, False, False, False, False, False, True]


def test_continuous_motion_is_rate_limited_and_keeps_the_newest_frame():
    clock = FakeClock()
    gate = FrameGate(clock=clock, max_motion_interval_s=8.0)
    kept_at = []
    for second in range(30):
        clock.now = float(second)
        if gate.changed(noise(second))[0]:
            kept_at.append(second)
    assert kept_at[0] == 0
    gaps = [b - a for a, b in zip(kept_at, kept_at[1:])]
    assert gaps and all(gap >= 8 for gap in gaps)
    assert len(kept_at) <= 1 + 30 // 8
    assert gate.last_change["kind"] == "motion"


def test_reset_makes_the_next_frame_kept():
    gate = FrameGate()
    base = slide(1)
    gate.changed(base)
    assert gate.changed(base.copy())[0] is False
    gate.reset()
    assert gate.last_change is None
    assert gate.changed(base.copy())[0] is True


def test_paragraph_added_to_a_slide_is_kept_as_a_region_update():
    gate = FrameGate()
    plain, extended = slide(3), slide(3, paragraph=True)
    assert feed(gate, [plain, extended, extended.copy()]) == [True, False, True]
    change = gate.last_change
    assert 0.03 <= change["fraction"] < 0.35
    assert gate.scene_cut is False
    x1, y1, x2, y2 = change["bbox"]
    assert x1 <= 80 and y1 <= 420 and x2 >= 700 and y2 >= 420 + 3 * 30 + 14
    assert y1 >= 300 and y2 <= 600


def test_full_slide_change_is_a_scene_cut():
    gate = FrameGate()
    first = slide(1)
    second = Image.new("RGB", SIZE, (20, 20, 30))
    text_lines(ImageDraw.Draw(second), 100, 100, 18, 1000, seed=9)
    feed(gate, [first, second, second.copy()])
    assert gate.last_change["fraction"] >= 0.35
    assert gate.scene_cut is True


def test_token_is_sixteen_hex_digits():
    gate = FrameGate()
    _, token = gate.changed(slide(1))
    assert re.fullmatch(r"[0-9a-f]{16}", token)
    _, again = gate.changed(slide(1))
    assert again == token


def test_per_frame_cost_on_1080p_is_small():
    gate = FrameGate()
    frames = [slide(seed).resize((1920, 1080)) for seed in range(4)]
    gate.changed(frames[0])
    started = time.perf_counter()
    rounds = 20
    for index in range(rounds):
        gate.changed(frames[index % 4])
    per_frame_ms = (time.perf_counter() - started) * 1000 / rounds
    print(f"FrameGate per 1080p frame: {per_frame_ms:.2f} ms")
    assert per_frame_ms < 60
