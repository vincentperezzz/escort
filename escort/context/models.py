from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ContextSection:
    anchor: str
    heading: str
    quote: str
    body: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"anchor": self.anchor, "heading": self.heading, "quote": self.quote, "body": self.body}


@dataclass
class ContextItem:
    id: str
    source_kind: str
    source_label: str
    title: str
    sections: list[ContextSection] = field(default_factory=list)
    enabled: bool = True
    external_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sections"] = [section.to_dict() for section in self.sections]
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ContextItem:
        sections = [
            ContextSection(
                anchor=str(item.get("anchor") or ""),
                heading=str(item.get("heading") or ""),
                quote=str(item.get("quote") or ""),
                body=str(item.get("body") or ""),
            )
            for item in raw.get("sections") or []
        ]
        return cls(
            id=str(raw["id"]),
            source_kind=str(raw.get("source_kind") or "upload"),
            source_label=str(raw.get("source_label") or "Upload"),
            title=str(raw.get("title") or "Untitled"),
            sections=sections,
            enabled=bool(raw.get("enabled", True)),
            external_id=raw.get("external_id"),
        )


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: str
    end: str
    description: str = ""
    attendees: list[str] = field(default_factory=list)
    linked_doc_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MatchProposal:
    item_id: str
    title: str
    source_label: str
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
