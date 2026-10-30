from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CARD_TYPES = ("PRD_CONFLICT", "COMMITMENT", "SUGGESTED_REPLY")


@dataclass
class Turn:
    speaker: str
    text: str
    start_ms: int = 0
    end_ms: int = 0
    turn_id: int | None = None
    vendor_speaker: str | None = None


@dataclass
class CardDraft:
    type: str
    title: str
    body: str
    prd_section: str | None = None
    who: str | None = None
    what: str | None = None


@dataclass
class DeicticHint:
    region: str
    noun: str
    phrase: str


@dataclass
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    w: int
    h: int
    score: float | None = None

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2


@dataclass
class Utterance:
    id: str
    speaker: str
    text: str
    start_ms: int
    end_ms: int
    finished_at: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("finished_at", None)
        return data


@dataclass
class FrameRecord:
    id: str
    path: str
    phash: str
    captured_at: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("path", None)
        data["changed"] = True
        return data


@dataclass
class CardRecord:
    id: str
    type: str
    title: str
    body: str
    prd_section: str | None
    who: str | None
    what: str | None
    utterance_id: str | None
    trigger_text: str | None
    created_at: str
    latency_ms: float
    crop_path: str | None
    crop_url: str | None
    source: str
    deictic: str | None
    segmenter: str | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("crop_path", None)
        return data
