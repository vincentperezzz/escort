from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from escort.models import CARD_TYPES, NUDGE_KINDS, CardRecord, FrameRecord, NudgeRecord, SnapshotRecord, Utterance

RETENTION_NOTE = (
    "One meeting lives here: PRD sections, Me and Them turns, commitments, open items, and the last changed frame. "
    "Raw audio is not kept. Older frames are dropped as soon as a new one arrives, except the few kept as "
    "visuals for the notes (charts, diagrams, sketches). "
    "delete_meeting_state throws the whole meeting away when the call is over."
)
LATENCY_TARGET_MS = 5000
MAX_SNAPSHOTS = 40


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "p50_ms": None,
            "p95_ms": None,
            "target_ms": LATENCY_TARGET_MS,
            "over_target": 0,
        }
    ordered = sorted(values)

    def pick(fraction: float) -> float:
        if len(ordered) == 1:
            return round(ordered[0], 1)
        rank = (len(ordered) - 1) * fraction
        low = int(rank)
        high = min(low + 1, len(ordered) - 1)
        weight = rank - low
        return round(ordered[low] * (1 - weight) + ordered[high] * weight, 1)

    return {
        "count": len(ordered),
        "p50_ms": pick(0.50),
        "p95_ms": pick(0.95),
        "target_ms": LATENCY_TARGET_MS,
        "over_target": sum(1 for value in ordered if value > LATENCY_TARGET_MS),
    }


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
        self.nudges: list[NudgeRecord] = []
        self.snapshots: list[SnapshotRecord] = []
        self.jots: list[dict[str, str]] = []  # the user's own notes typed during the call
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
        self.prd_sections: list[dict[str, str]] = []
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
        vendor_speaker: str | None = None,
    ) -> Utterance:
        item = Utterance(
            id=new_id("utt"),
            speaker=speaker,
            text=text.strip(),
            start_ms=start_ms,
            end_ms=end_ms,
            finished_at=finished_at,
            created_at=utc_now(),
            vendor_speaker=vendor_speaker,
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
            previous = self.frames[-1] if self.frames else None
            self.frames = [record]
        if previous and previous.path != record.path:
            self._unlink(Path(previous.path))
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

    def add_nudge(self, nudge: NudgeRecord) -> NudgeRecord:
        if nudge.kind not in NUDGE_KINDS:
            raise ValueError(f"unknown nudge kind {nudge.kind}")
        with self.lock:
            self.nudges.append(nudge)
            self.nudges = self.nudges[-60:]
        return nudge

    def nudge(self, nudge_id: str) -> NudgeRecord | None:
        with self.lock:
            return next((item for item in self.nudges if item.id == nudge_id), None)

    def set_nudge_status(self, nudge_id: str, status: str) -> NudgeRecord | None:
        with self.lock:
            item = self.nudge(nudge_id)
            if item is not None:
                item.status = status
            return item

    def add_jot(self, text: str) -> dict[str, str]:
        """A line the user typed during the call. It is the skeleton of the notes written afterwards."""
        with self.lock:
            last = self.utterances[-1].text if self.utterances else ""
            jot = {"id": new_id("jot"), "text": text.strip()[:500], "at": utc_now(), "after": last[:160]}
            self.jots.append(jot)
        return jot

    def add_snapshot(self, image: Any, *, caption: str, kind: str) -> SnapshotRecord:
        """Keep a frame for the notes. Oldest visuals go first past MAX_SNAPSHOTS."""
        path = self.save_png(image, "visual")
        record = SnapshotRecord(
            id=new_id("vis"),
            path=str(path),
            url=f"/panel/media/{path.name}",
            caption=caption.strip(),
            kind=kind,
            captured_at=utc_now(),
        )
        with self.lock:
            self.snapshots.append(record)
            dropped = self.snapshots[:-MAX_SNAPSHOTS]
            self.snapshots = self.snapshots[-MAX_SNAPSHOTS:]
        for item in dropped:
            self._unlink(Path(item.path))
        return record

    def meeting(self) -> dict[str, Any]:
        with self.lock:
            commitments = []
            open_items = []
            for card in self.cards:
                if card.type == "COMMITMENT":
                    commitments.append(
                        {
                            "id": card.id,
                            "who": card.who,
                            "what": card.what,
                            "when": card.when or "unspecified",
                            "utterance_id": card.utterance_id,
                        }
                    )
                if card.type == "PRD_CONFLICT":
                    open_items.append(
                        {
                            "id": card.id,
                            "title": card.title,
                            "prd_section": card.prd_section,
                            "status": "open",
                        }
                    )
            last = self.frames[-1].to_dict() if self.frames else None
            return {
                "prd_sections": list(self.prd_sections),
                "commitments": commitments,
                "turns": [item.to_dict() for item in self.utterances],
                "open_items": open_items,
                "last_frame": last,
                "latency": latency_summary([card.latency_ms for card in self.cards]),
                "retention": RETENTION_NOTE,
            }

    def delete_meeting(self) -> dict[str, Any]:
        with self.lock:
            paths = [Path(frame.path) for frame in self.frames]
            paths.extend(Path(card.crop_path) for card in self.cards if card.crop_path)
            paths.extend(Path(item.path) for item in self.snapshots)
            self.utterances.clear()
            self.frames.clear()
            self.cards.clear()
            self.nudges.clear()
            self.snapshots.clear()
            self.jots.clear()
            self.partials.clear()
            self.warnings.clear()
            self.skipped_frames = 0
            self._dedupe.clear()
            self.mode = "deleted"
        for path in paths:
            self._unlink(path)
        if self.media_dir.exists():
            for path in self.media_dir.glob("*"):
                if path.is_file():
                    self._unlink(path)
        return {"deleted": True, "retention": RETENTION_NOTE}

    def _unlink(self, path: Path) -> None:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass

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
            meeting = self.meeting()
            return {
                "title": self.title,
                "mode": self.mode,
                "consent": self.consent,
                "retention": RETENTION_NOTE,
                "transcriber": self.transcriber,
                "spark": self.spark,
                "segmenter": self.segmenter,
                "prd_name": self.prd_name,
                "prd_sections": list(self.prd_sections),
                "started_at": self.started_at,
                "utterances": [item.to_dict() for item in self.utterances],
                "partials": dict(self.partials),
                "cards": [item.to_dict() for item in self.cards],
                "nudges": [item.to_dict() for item in self.nudges],
                "snapshots": [item.to_dict() for item in self.snapshots],
                "jots": [dict(item) for item in self.jots],
                "frames": [item.to_dict() for item in self.frames],
                "skipped_frames": self.skipped_frames,
                "warnings": list(self.warnings),
                "latest_frame_url": latest,
                "meeting": meeting,
                "latency": meeting["latency"],
                "server_time": utc_now(),
            }
