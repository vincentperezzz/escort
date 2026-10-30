from __future__ import annotations

from PIL import Image

from taglish_listener.capture.windows_audio import CaptureUnavailable


class ScreenGrabber:
    def __init__(self, monitor: int = 1) -> None:
        try:
            import mss
        except ImportError as exc:
            raise CaptureUnavailable("mss is required for screen frames. pip install mss") from exc
        self._mss = mss.mss()
        self._monitor = monitor

    def grab(self) -> Image.Image:
        monitors = self._mss.monitors
        index = self._monitor if self._monitor < len(monitors) else 1
        shot = self._mss.grab(monitors[index])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def close(self) -> None:
        self._mss.close()
