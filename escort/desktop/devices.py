from __future__ import annotations

import time
from typing import Any

from escort.capture.portaudio import LOCK as PORTAUDIO_LOCK

# /api/status is polled every ~1.2 s by the overlay and the main window at once;
# re-initialising PortAudio for each poll is slow and (with the capture thread
# running) was the source of a native crash. Re-enumerate at most this often.
CACHE_S = 10.0
FALLBACK = [
    {"id": "default-mic", "name": "Default microphone", "kind": "mic"},
    {"id": "default-loop", "name": "Default speakers (loopback)", "kind": "loopback"},
]

_cache: tuple[float, list[dict[str, str]]] | None = None


def list_audio_devices(fresh: bool = False, pyaudio_module: Any = None) -> list[dict[str, str]]:
    """WASAPI mics, outputs and loopbacks. Thread-safe; cached for CACHE_S seconds."""
    global _cache
    with PORTAUDIO_LOCK:
        cached = _cache
        if not fresh and cached is not None and time.monotonic() - cached[0] < CACHE_S:
            return [dict(item) for item in cached[1]]
        devices = _enumerate(pyaudio_module)
        _cache = (time.monotonic(), devices)
        return [dict(item) for item in devices]


def _enumerate(pyaudio_module: Any = None) -> list[dict[str, str]]:
    pyaudio = pyaudio_module
    if pyaudio is None:
        try:
            import pyaudiowpatch as pyaudio
        except Exception:
            return [dict(item) for item in FALLBACK]
    pa = pyaudio.PyAudio()
    devices: list[dict[str, str]] = []
    try:
        host = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        for index in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(index)
            kind = "mic" if int(info.get("maxInputChannels") or 0) > 0 else "output"
            if info.get("isLoopbackDevice"):
                kind = "loopback"
            if int(info.get("hostApi") or -1) != int(host.get("index") or -1) and kind != "loopback":
                continue
            devices.append({"id": str(index), "name": str(info.get("name") or f"Device {index}"), "kind": kind})
    finally:
        pa.terminate()
    return devices or [dict(item) for item in FALLBACK]
