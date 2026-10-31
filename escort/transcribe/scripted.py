from __future__ import annotations

from pathlib import Path

from escort.audio_util import wav_duration_ms
from escort.models import Turn


class ScriptedTranscriber:
    name = "scripted-fixture"

    def __init__(self, lines: dict[str, str]) -> None:
        self._lines = lines

    def transcribe_file(self, path: Path, *, speaker: str) -> list[Turn]:
        duration = wav_duration_ms(path)
        text = self._lines.get(path.name) or self._lines.get(str(path))
        if text is None:
            raise KeyError(f"no scripted line for {path.name}")
        return [Turn(speaker=speaker, text=text, start_ms=0, end_ms=duration)]
