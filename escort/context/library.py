from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from escort.context.chunk import sections_from_markdown
from escort.context.extract import extract_text
from escort.context.matcher import match_event
from escort.context.models import CalendarEvent, ContextItem, MatchProposal
from escort.context.sources.base import ConnectorError, ConnectorSetupError
from escort.context.sources.registry import Connectors, build_sources
from escort.store import MeetingStore, new_id

REFRESHABLE_KINDS = {"google_docs", "google_sheets", "google_slides", "google_drive"}


class ContextLibrary:
    def __init__(
        self,
        root: Path,
        secrets_get: Callable[[str], str | None],
        connectors: Connectors | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "context.json"
        self.secrets_get = secrets_get
        self.connectors = connectors or Connectors.from_getter(secrets_get)
        self.sources = build_sources(self.connectors)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.data: dict[str, Any] = {"items": [], "prep": {}, "chat": []}
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.data = {"items": [], "prep": {}, "chat": []}
        chat = self.data.get("chat")
        if isinstance(chat, list):
            cleaned = [
                turn
                for turn in chat
                if not (
                    isinstance(turn, dict)
                    and turn.get("role") == "assistant"
                    and (
                        "[Errno 11001]" in str(turn.get("content") or "")
                        or "getaddrinfo failed" in str(turn.get("content") or "")
                    )
                )
            ]
            if len(cleaned) != len(chat):
                self.data["chat"] = cleaned
                self._save()

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def items(self) -> list[ContextItem]:
        return [ContextItem.from_dict(raw) for raw in self.data.get("items") or []]

    def add_item(self, item: ContextItem) -> ContextItem:
        raw_items = self.data.setdefault("items", [])
        raw_items.append(item.to_dict())
        self._save()
        return item

    def add_upload(self, filename: str, data: bytes) -> ContextItem:
        text = extract_text(filename, data)
        sections = sections_from_markdown(text)
        item = ContextItem(
            id=new_id("ctx"),
            source_kind="upload",
            source_label="Upload",
            title=filename,
            sections=sections,
            enabled=True,
            external_id=None,
        )
        uploads = self.root / "uploads"
        uploads.mkdir(exist_ok=True)
        safe = filename.replace("/", "_").replace("\\", "_")
        (uploads / f"{item.id}_{safe}").write_bytes(data)
        return self.add_item(item)

    def add_paste(self, text: str, title: str = "Pasted notes") -> ContextItem:
        item = ContextItem(
            id=new_id("ctx"),
            source_kind="paste",
            source_label="Notes",
            title=title,
            sections=sections_from_markdown(text),
            enabled=True,
        )
        return self.add_item(item)

    def remove_item(self, item_id: str) -> bool:
        before = len(self.data.get("items") or [])
        self.data["items"] = [raw for raw in self.data.get("items") or [] if raw.get("id") != item_id]
        self._save()
        return len(self.data["items"]) < before

    def set_enabled(self, item_id: str, enabled: bool) -> None:
        for raw in self.data.get("items") or []:
            if raw.get("id") == item_id:
                raw["enabled"] = enabled
        self._save()

    def import_external(self, source_name: str, external_id: str) -> ContextItem:
        source = self._source(source_name)
        return self._replace_or_add(source.fetch(external_id))

    def import_drive_file(self, file_id: str, mime_type: str = "", name: str = "") -> ContextItem:
        """A file picked in Google's Drive chooser (drive.file: only that file is reachable)."""
        return self._replace_or_add(self.connectors.fetch_drive_file(file_id, mime_type, name))

    def _replace_or_add(self, item: ContextItem) -> ContextItem:
        if item.source_kind in REFRESHABLE_KINDS and item.external_id:
            # Importing the same Google file again refreshes the item instead of duplicating it.
            self.data["items"] = [
                raw
                for raw in self.data.get("items") or []
                if not (raw.get("source_kind") == item.source_kind and raw.get("external_id") == item.external_id)
            ]
        return self.add_item(item)

    def connector_status(self) -> list[dict[str, Any]]:
        rows = []
        for name, source in self.sources.items():
            rows.append(
                {
                    "name": name,
                    "label": source.label,
                    "connected": source.connected(),
                    "mode": source.mode(),
                }
            )
        return rows

    def _source(self, source_name: str):
        source = self.sources.get(source_name)
        if source is None:
            raise ConnectorSetupError(source_name or "unknown", f"Unknown connector: {source_name!r}")
        return source

    def search_connector(self, source_name: str, query: str) -> list[dict]:
        return self._source(source_name).search(query)

    def upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        try:
            return self.sources["google_calendar"].list_upcoming_events(limit)
        except ConnectorError:
            return []

    def propose_matches(self, event_id: str) -> tuple[CalendarEvent | None, list[MatchProposal]]:
        event = next((item for item in self.upcoming_events(10) if item.id == event_id), None)
        if event is None:
            return None, []
        proposals = match_event(event, self.items())
        self.data.setdefault("prep", {})["selected_event_id"] = event_id
        self.data["prep"]["proposals"] = [item.to_dict() for item in proposals]
        self._save()
        return event, proposals

    def confirm_prep(self, event_id: str, item_ids: list[str]) -> dict[str, Any]:
        prep = self.data.setdefault("prep", {})
        prep["selected_event_id"] = event_id
        prep["confirmed_ids"] = list(item_ids)
        self._save()
        return prep

    def prep_summary(self, event_id: str | None = None) -> str:
        prep = self.data.get("prep") or {}
        eid = event_id or prep.get("selected_event_id")
        event = next((item for item in self.upcoming_events(10) if item.id == eid), None)
        if event is None:
            event = self.upcoming_events(1)[0] if self.upcoming_events(1) else None
        if event is None:
            return "No upcoming meeting on the calendar."
        _, proposals = self.propose_matches(event.id)
        confirmed = set(prep.get("confirmed_ids") or [])
        if not confirmed and proposals:
            confirmed = {item.item_id for item in proposals[:2]}
        lines = [f"Prep for {event.title} ({event.start})", ""]
        if not confirmed:
            lines.append("No context items matched yet. Add notes, connect Notion or paste a Google Docs, Sheets or Slides link.")
            return "\n".join(lines)
        by_id = {item.id: item for item in self.items()}
        for item_id in confirmed:
            item = by_id.get(item_id)
            if not item:
                continue
            lines.append(f"- {item.source_label} · {item.title}")
            for section in item.sections[:2]:
                lines.append(f"  · {section.heading}: {section.quote[:160]}")
        return "\n".join(lines)

    def apply_to_store(self, store: MeetingStore) -> None:
        enabled = [item for item in self.items() if item.enabled]
        sections: list[dict[str, str]] = []
        chunks: list[str] = []
        for item in enabled:
            chunks.append(f"# {item.source_label} · {item.title}\n")
            for section in item.sections:
                citation = f"{item.source_label} · {section.heading}"
                sections.append(
                    {
                        "anchor": section.anchor,
                        "heading": section.heading,
                        "quote": section.quote,
                        "source_label": citation,
                    }
                )
                chunks.append(f"## {section.heading}\n{section.body or section.quote}\n")
        store.prd_sections = sections
        store.prd_name = "context_bundle"
        if chunks:
            path = store.media_dir.parent / "context_bundle.md"
            path.write_text("\n".join(chunks), encoding="utf-8")

    def combined_markdown(self) -> str:
        parts = []
        for item in self.items():
            if not item.enabled:
                continue
            parts.append(f"# {item.source_label} · {item.title}")
            for section in item.sections:
                parts.append(f"## {section.heading}")
                parts.append(section.body or section.quote)
        return "\n\n".join(parts)

    def chat_history(self) -> list[dict[str, str]]:
        return list(self.data.get("chat") or [])

    def append_chat(self, role: str, content: str, changed: list[dict[str, Any]] | None = None) -> None:
        turn: dict[str, Any] = {"role": role, "content": content}
        if changed:
            turn["changed"] = changed  # settings this reply changed, shown as chips under it
        self.data.setdefault("chat", []).append(turn)
        self.data["chat"] = self.data["chat"][-40:]
        self._save()

    def switch_root(self, new_root: Path) -> None:
        self.root = new_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "context.json"
        self._load()

    def clear_chat(self) -> None:
        self.data["chat"] = []
        self._save()

    def clear(self) -> None:
        self.data = {"items": [], "prep": {}, "chat": []}
        self._save()

    def public_view(
        self,
        active_project: Any | None = None,
        projects: list[Any] | None = None,
        upcoming: list[CalendarEvent] | None = None,
    ) -> dict[str, Any]:
        prep = self.data.get("prep") or {}
        upcoming_rows = upcoming if upcoming is not None else self.upcoming_events(6)
        return {
            "items": [item.to_dict() for item in self.items()],
            "connectors": self.connector_status(),
            "upcoming": [event.to_dict() for event in upcoming_rows],
            "prep": prep,
            "chat": self.chat_history(),
            "active_project": active_project.to_dict() if active_project and hasattr(active_project, "to_dict") else None,
            "projects": [p.to_dict() if hasattr(p, "to_dict") else p for p in projects or []],
        }
