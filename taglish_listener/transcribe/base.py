from __future__ import annotations

from pathlib import Path
from typing import Protocol

from taglish_listener.models import Turn


class Transcriber(Protocol):
    name: str

    def transcribe_file(self, path: Path, *, speaker: str) -> list[Turn]:
        ...


class TranscriberUnavailable(RuntimeError):
    pass
