from __future__ import annotations

import array
import sys
import wave
from pathlib import Path


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        if rate <= 0:
            return 0
        return int(handle.getnframes() * 1000 / rate)


def _samples(data: bytes) -> array.array:
    usable = data[: len(data) // 2 * 2]
    samples = array.array("h")
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _pack(samples: array.array) -> bytes:
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def pcm_to_mono_24k(
    data: bytes,
    sample_width: int,
    channels: int,
    rate: int,
    state: tuple | None,
) -> tuple[bytes, tuple | None]:
    del state
    if sample_width != 2:
        raise ValueError("only 16-bit PCM is supported")
    samples = _samples(data)
    if channels > 1:
        mono = array.array("h")
        for offset in range(0, len(samples) - channels + 1, channels):
            acc = sum(samples[offset + index] for index in range(channels))
            mono.append(int(acc / channels))
        samples = mono
    if rate != 24000 and len(samples):
        out_len = max(1, int(round(len(samples) * 24000 / rate)))
        out = array.array("h")
        last = len(samples) - 1
        for index in range(out_len):
            pos = index * rate / 24000
            left = min(int(pos), last)
            right = min(left + 1, last)
            frac = pos - left
            value = samples[left] * (1 - frac) + samples[right] * frac
            out.append(max(-32768, min(32767, int(value))))
        samples = out
    return _pack(samples), None


def rms_s16(data: bytes) -> int:
    samples = _samples(data)
    if not samples:
        return 0
    total = sum(sample * sample for sample in samples)
    return int((total / len(samples)) ** 0.5)


def write_wav(path: Path, pcm: bytes, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
