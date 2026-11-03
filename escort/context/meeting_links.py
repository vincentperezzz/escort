from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from escort.context.models import CalendarEvent

LinkedBy = Literal["ai", "user"]


def _parse_iso(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ProjectMeetingsStore:
    def __init__(self, project_dir: Path) -> None:
        self.path = project_dir / "meetings.json"
        self._data: dict[str, Any] = {"linked": {}, "excluded": []}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        linked = raw.get("linked") if isinstance(raw.get("linked"), dict) else {}
        excluded = raw.get("excluded") if isinstance(raw.get("excluded"), list) else []
        self._data = {"linked": linked, "excluded": [str(x) for x in excluded if str(x).strip()]}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def excluded_ids(self) -> set[str]:
        return {str(x) for x in self._data.get("excluded") or []}

    def linked_entries(self) -> dict[str, dict[str, Any]]:
        raw = self._data.get("linked") or {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    def apply_user_changes(self, add: list[str], remove: list[str], events_by_id: dict[str, CalendarEvent]) -> None:
        excluded = set(self.excluded_ids())
        linked = self.linked_entries()
        for event_id in remove:
            eid = str(event_id).strip()
            if not eid:
                continue
            linked.pop(eid, None)
            excluded.add(eid)
        for event_id in add:
            eid = str(event_id).strip()
            if not eid:
                continue
            excluded.discard(eid)
            event = events_by_id.get(eid)
            if event is None:
                continue
            linked[eid] = _event_record(event, "user")
        self._data["linked"] = linked
        self._data["excluded"] = sorted(excluded)
        self._save()

    def apply_ai_links(
        self,
        assignments: dict[str, CalendarEvent | None],
        events_by_id: dict[str, CalendarEvent],
    ) -> None:
        excluded = self.excluded_ids()
        linked = self.linked_entries()
        for event_id, entry in list(linked.items()):
            if str(entry.get("linked_by") or "") == "user":
                event = events_by_id.get(event_id)
                if event is not None:
                    linked[event_id] = _event_record(event, "user")
                continue
            if event_id in excluded:
                linked.pop(event_id, None)
                continue
            if assignments.get(event_id) is None:
                linked.pop(event_id, None)
        for event_id, event in assignments.items():
            if event is None or event_id in excluded:
                continue
            existing = linked.get(event_id)
            if existing and str(existing.get("linked_by") or "") == "user":
                linked[event_id] = _event_record(events_by_id.get(event_id) or event, "user")
                continue
            linked[event_id] = _event_record(event, "ai")
        self._data["linked"] = linked
        self._save()

    def refresh_metadata(self, events_by_id: dict[str, CalendarEvent]) -> None:
        linked = self.linked_entries()
        changed = False
        for event_id, entry in linked.items():
            event = events_by_id.get(event_id)
            if event is None:
                continue
            updated = _event_record(event, str(entry.get("linked_by") or "ai"))
            if updated != entry:
                linked[event_id] = updated
                changed = True
        if changed:
            self._data["linked"] = linked
            self._save()

    def meetings_payload(self) -> dict[str, Any]:
        linked = self.linked_entries()
        meetings = []
        for event_id in sorted(linked.keys(), key=lambda eid: linked[eid].get("start") or ""):
            entry = linked[event_id]
            meetings.append(
                {
                    "event_id": event_id,
                    "title": str(entry.get("title") or ""),
                    "start": str(entry.get("start") or ""),
                    "end": str(entry.get("end") or ""),
                    "attendees": list(entry.get("attendees") or []),
                    "linked_by": str(entry.get("linked_by") or "ai"),
                }
            )
        nxt = _next_meeting(meetings)
        return {"meetings": meetings, "next": nxt}


def _event_record(event: CalendarEvent, linked_by: LinkedBy) -> dict[str, Any]:
    return {
        "linked_by": linked_by,
        "title": event.title,
        "start": event.start,
        "end": event.end,
        "attendees": list(event.attendees),
    }


def _next_meeting(meetings: list[dict[str, Any]]) -> dict[str, Any] | None:
    now = _now_utc()
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    for row in meetings:
        start = _parse_iso(str(row.get("start") or ""))
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start >= now:
            upcoming.append((start, row))
    if not upcoming:
        return None
    upcoming.sort(key=lambda pair: pair[0])
    return dict(upcoming[0][1])


def project_context_titles(project_dir: Path) -> list[str]:
    path = project_dir / "context.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    titles: list[str] = []
    for raw in data.get("items") or []:
        if isinstance(raw, dict):
            title = str(raw.get("title") or "").strip()
            if title:
                titles.append(title)
    return titles


def index_event_project_map(projects_dir: Path, project_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pid in project_ids:
        store = ProjectMeetingsStore(projects_dir / pid)
        for event_id in store.linked_entries():
            out[event_id] = pid
    return out
