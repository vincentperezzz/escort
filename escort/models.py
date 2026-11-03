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
    when: str | None = None


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
    vendor_speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("finished_at", None)
        if data.get("vendor_speaker") is None:
            data.pop("vendor_speaker", None)
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
    when: str | None = None
    proactive: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("crop_path", None)
        return data


NUDGE_KINDS = (
    "NO_MEETING",  # listening, but nothing says a call is happening (missing link, postponed?)
    "WRONG_PROJECT",  # the screen or talk belongs to another project than the active one
    "NOTED",  # quiet acknowledgement that a note was captured
    "CLARIFY",  # something vague was agreed: no owner, date, number or definition
    "QUESTION_FOR_YOU",  # someone asked Me something that is still unanswered
    "TIME_CHECK",  # the slot is nearly over with open items left
    "OFF_TOPIC",  # the talk drifted away from the meeting's purpose
    "HEADS_UP",  # anything else the model judges worth a tap on the shoulder
)
NUDGE_PRIORITIES = ("low", "normal", "urgent")


@dataclass
class NudgeRecord:
    """A proactive card: Escort speaking up on its own, with optional one-click actions.

    Separate from CardRecord so the three scope-guard card types stay as they are.
    actions: [{"id": "open_link" | "switch_project" | "ask" | "dismiss" | ..., "label": str, "value": str?}]
    """

    id: str
    kind: str
    priority: str
    title: str
    body: str
    actions: list[dict[str, str]]
    created_at: str
    source: str
    key: str
    reason: str = ""
    status: str = "open"  # open | dismissed | acted

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SnapshotRecord:
    """A frame kept for the notes because it shows something text can't carry (chart, diagram, sketch)."""

    id: str
    path: str
    url: str
    caption: str
    kind: str
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("path", None)
        return data
