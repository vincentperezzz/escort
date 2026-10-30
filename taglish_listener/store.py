from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taglish_listener.models import CARD_TYPES, CardRecord, FrameRecord, Utterance


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class MeetingStore:
    def __init__(self, media_dir: Path) -> None:
        self.media_dir = media_dir
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.utterances: list[Utterance] = []
        self.frames: list[FrameRecord] = []
        self.cards: list[CardRecord] = []
        self.skipped_frames = 0
        self.warnings: list[str] = []
        self.partials: dict[str, str] = {}
        self.mode = "idle"
        self.title = ""
        self.consent = ""
        self.transcriber = ""
        self.spark = ""
        self.segmenter = ""
        self.prd_name = ""
        self.started_at = utc_now()
        self._dedupe: set[tuple[Any, ...]] = set()

    def warn(self, message: str) -> None:
        with self.lock:
            self.warnings.append(message)
            self.warnings = self.warnings[-20:]

    def add_utterance(
        self,
        *,
        speaker: str,
        text: str,
        start_ms: int,
        end_ms: int,
        finished_at: float,
    ) -> Utterance:
        item = Utterance(
            id=new_id("utt"),
            speaker=speaker,
            text=text.strip(),
            start_ms=start_ms,
            end_ms=end_ms,
            finished_at=finished_at,
            created_at=utc_now(),
        )
        with self.lock:
            self.utterances.append(item)
            self.partials.pop(speaker, None)
        return item

    def set_partial(self, speaker: str, text: str) -> None:
        with self.lock:
            self.partials[speaker] = text

    def add_frame(self, *, path: Path, phash: str) -> FrameRecord:
        name = path.name
        record = FrameRecord(
            id=new_id("frm"),
            path=str(path),
            phash=phash,
            captured_at=utc_now(),
            url=f"/panel/media/{name}",
        )
        with self.lock:
            self.frames.append(record)
        return record

    def note_skipped_frame(self) -> None:
        with self.lock:
            self.skipped_frames += 1

    def latest_frame(self) -> FrameRecord | None:
        with self.lock:
            return self.frames[-1] if self.frames else None

    def latest_utterance(self) -> Utterance | None:
        with self.lock:
            return self.utterances[-1] if self.utterances else None

    def utterance(self, utterance_id: str) -> Utterance | None:
        with self.lock:
            for item in self.utterances:
                if item.id == utterance_id:
                    return item
        return None

    def claim(self, key: tuple[Any, ...]) -> bool:
        with self.lock:
            if key in self._dedupe:
                return False
            self._dedupe.add(key)
            return True

    def add_card(self, card: CardRecord) -> CardRecord:
        if card.type not in CARD_TYPES:
            raise ValueError(f"unknown card type {card.type}")
        with self.lock:
            self.cards.append(card)
        return card

    def save_png(self, image: Any, stem: str) -> Path:
        name = f"{stem}_{uuid.uuid4().hex[:8]}.png"
        path = self.media_dir / name
        image.save(path, format="PNG")
        return path

    def resolve_media(self, name: str) -> Path | None:
        root = self.media_dir.resolve()
        target = (root / name).resolve()
        if target.parent != root or not target.is_file():
            return None
        return target

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            latest = self.frames[-1].url if self.frames else None
            return {
                "title": self.title,
                "mode": self.mode,
                "consent": self.consent,
                "transcriber": self.transcriber,
                "spark": self.spark,
                "segmenter": self.segmenter,
                "prd_name": self.prd_name,
                "started_at": self.started_at,
                "utterances": [item.to_dict() for item in self.utterances],
                "partials": dict(self.partials),
                "cards": [item.to_dict() for item in self.cards],
                "frames": [item.to_dict() for item in self.frames],
                "skipped_frames": self.skipped_frames,
                "warnings": list(self.warnings),
                "latest_frame_url": latest,
                "server_time": utc_now(),
            }
