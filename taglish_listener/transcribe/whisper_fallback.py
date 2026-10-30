from __future__ import annotations

from pathlib import Path

from taglish_listener.models import Turn
from taglish_listener.transcribe.base import TranscriberUnavailable


class WhisperTranscriber:
    name = "faster-whisper"

    def __init__(self, model_size: str = "small") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriberUnavailable(
                "faster-whisper is not installed. pip install 'taglish-listener[whisper]'"
            ) from exc
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.name = f"faster-whisper-{model_size}"

    def transcribe_file(self, path: Path, *, speaker: str) -> list[Turn]:
        segments, _info = self._model.transcribe(
            str(path),
            vad_filter=True,
            initial_prompt="Taglish code-switching between Tagalog and English. BayaniPay, Phase 1, Phase 2, InstaPay.",
        )
        turns: list[Turn] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            turns.append(
                Turn(
                    speaker=speaker,
                    text=text,
                    start_ms=int(segment.start * 1000),
                    end_ms=int(segment.end * 1000),
                )
            )
        return turns
