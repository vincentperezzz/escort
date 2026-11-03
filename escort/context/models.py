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
    mock: bool = False

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
            mock=bool(raw.get("mock", False)),
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
    mock: bool = False
    meeting_url: str = ""

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


@dataclass
class Project:
    id: str
    name: str
    description: str = ""
    calendar_keywords: list[str] = field(default_factory=list)
    calendar_domains: list[str] = field(default_factory=list)
    folder_path: str = ""
    auto_switch: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Project:
        return cls(
            id=str(raw.get("id") or ""),
            name=str(raw.get("name") or "Untitled Project"),
            description=str(raw.get("description") or ""),
            calendar_keywords=[str(k).strip() for k in raw.get("calendar_keywords") or [] if str(k).strip()],
            calendar_domains=[str(d).strip().lower() for d in raw.get("calendar_domains") or [] if str(d).strip()],
            folder_path=str(raw.get("folder_path") or ""),
            auto_switch=bool(raw.get("auto_switch", True)),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
        )
