from __future__ import annotations

from PIL import Image


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


class FrameGate:
    def __init__(self, hash_threshold: int = 6, mae_threshold: float = 8.0) -> None:
        self.hash_threshold = hash_threshold
        self.mae_threshold = mae_threshold
        self.prev_hash: int | None = None
        self.prev_image: Image.Image | None = None

    def reset(self) -> None:
        """Forget the baseline, so the next frame counts as changed (e.g. after switching display)."""
        self.prev_hash = None
        self.prev_image = None

    def changed(self, image: Image.Image) -> tuple[bool, str]:
        digest = dhash(image)
        token = f"{digest:016x}"
        if self.prev_hash is None or self.prev_image is None:
            self.prev_hash = digest
            self.prev_image = image
            return True, token
        distance = hamming(self.prev_hash, digest)
        drift = mean_abs_diff(self.prev_image, image)
        if distance >= self.hash_threshold or drift >= self.mae_threshold:
            self.prev_hash = digest
            self.prev_image = image
            return True, token
        return False, token
