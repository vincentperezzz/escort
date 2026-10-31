from __future__ import annotations

RATE = 24000
WIDTH = 2


def pcm_realtime_seconds(nbytes: int, rate: int = RATE) -> float:
    if nbytes <= 0:
        return 0.0
    return nbytes / (rate * WIDTH)


def iter_pcm_chunks(pcm: bytes, chunk_ms: int = 100, rate: int = RATE) -> list[bytes]:
    size = max(WIDTH, rate * WIDTH * chunk_ms // 1000)
    size -= size % WIDTH
    return [pcm[offset : offset + size] for offset in range(0, len(pcm), size) if pcm[offset : offset + size]]
