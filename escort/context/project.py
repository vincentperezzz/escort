from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from escort.context.extract import extract_text, sniff_kind
from escort.context.meeting_links import (
    ProjectMeetingsStore,
    index_event_project_map,
    project_context_titles,
)
from escort.context.meeting_matcher import match_events_with_ai
from escort.context.models import CalendarEvent, ContextItem, Project
from escort.store import new_id

log = logging.getLogger("escort.context.project")

DEFAULT_PROJECT_ID = "proj_default"
DEFAULT_PROJECT_NAME = "General"
DEFAULT_PROJECT_DESC = "General meetings and client calls"


class ProjectManager:
    """Manages workspace projects/topics with isolated context, files, and chat.

    Hierarchy:
    <base_dir>/projects/
        index.json                  # { active_id: str, projects: list[Project] }
        <proj_id>/
            context.json            # Items, prep proposals, chat history
            uploads/                # Project uploaded files
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.projects_dir = base_dir / "projects"
        self.index_path = self.projects_dir / "index.json"
        self._projects: dict[str, Project] = {}
        self.active_id: str = DEFAULT_PROJECT_ID
        self.dismissed_suggestion_id: str | None = None
        self._init_and_migrate()

    def _init_and_migrate(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            root_context = self.base_dir / "context.json"
            root_uploads = self.base_dir / "uploads"
            legacy = root_context.is_file() or root_uploads.is_dir()
            if legacy:
                default_proj = Project(
                    id=DEFAULT_PROJECT_ID,
                    name=DEFAULT_PROJECT_NAME,
                    description=DEFAULT_PROJECT_DESC,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
                self._projects = {default_proj.id: default_proj}
                self.active_id = default_proj.id
                default_dir = self.projects_dir / DEFAULT_PROJECT_ID
                default_dir.mkdir(parents=True, exist_ok=True)
                target_context = default_dir / "context.json"
                if root_context.is_file() and not target_context.exists():
                    try:
                        shutil.copy2(root_context, target_context)
                        log.info("Migrated existing root context.json to %s", target_context)
                    except Exception as exc:
                        log.warning("Could not copy existing context.json: %s", exc)
                target_uploads = default_dir / "uploads"
                if root_uploads.is_dir() and not target_uploads.exists():
                    try:
                        shutil.copytree(root_uploads, target_uploads)
                        log.info("Migrated existing uploads folder to %s", target_uploads)
                    except Exception as exc:
                        log.warning("Could not copy existing uploads: %s", exc)
            else:
                self._projects = {}
                self.active_id = ""
            self._save_index()
        else:
            self._load_index()

    def _load_index(self) -> None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Failed to load projects index: %s", exc)
            data = {}

        raw_list = data.get("projects") or []
        self._projects = {}
        for item in raw_list:
            if isinstance(item, dict) and item.get("id"):
                proj = Project.from_dict(item)
                self._projects[proj.id] = proj

        active = data.get("active_id")
        if active and active in self._projects:
            self.active_id = active
        elif self._projects:
            self.active_id = next(iter(self._projects.keys()))
        else:
            self.active_id = ""

    def _save_index(self) -> None:
        data = {
            "active_id": self.active_id,
            "projects": [p.to_dict() for p in self._projects.values()],
        }
        self.index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_projects(self) -> list[Project]:
        return list(self._projects.values())

    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def get_active_project(self) -> Project | None:
        if not self.active_id:
            return None
        return self.get_project(self.active_id)

    def needs_project(self) -> bool:
        return len(self._projects) == 0

    def get_project_dir(self, project_id: str) -> Path:
        proj_dir = self.projects_dir / project_id
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    def get_active_project_dir(self) -> Path:
        if not self.active_id:
            inactive = self.projects_dir / "_inactive"
            inactive.mkdir(parents=True, exist_ok=True)
            return inactive
        return self.get_project_dir(self.active_id)

    def set_active_project(self, project_id: str) -> Project:
        if project_id not in self._projects:
            raise KeyError(f"Project not found: {project_id}")
        self.active_id = project_id
        self._save_index()
        return self._projects[project_id]

    def create_project(
        self,
        name: str,
        description: str = "",
        calendar_keywords: list[str] | None = None,
        calendar_domains: list[str] | None = None,
        folder_path: str = "",
        auto_switch: bool = False,
    ) -> Project:
        clean_name = name.strip() or "Untitled Project"
        pid = new_id("proj")
        now = time.time()
        proj = Project(
            id=pid,
            name=clean_name,
            description=description.strip(),
            calendar_keywords=[],
            calendar_domains=[],
            folder_path="",
            auto_switch=auto_switch,
            created_at=now,
            updated_at=now,
        )
        _ = calendar_keywords, calendar_domains, folder_path
        self._projects[pid] = proj
        self.get_project_dir(pid)
        self._save_index()
        return proj

    def update_project(self, project_id: str, updates: dict[str, Any]) -> Project:
        proj = self.get_project(project_id)
        if proj is None:
            raise KeyError(f"Project not found: {project_id}")
        if "name" in updates and str(updates["name"]).strip():
            proj.name = str(updates["name"]).strip()
        if "description" in updates:
            proj.description = str(updates["description"]).strip()
        if "folder_path" in updates and str(updates["folder_path"]).strip():
            proj.folder_path = str(updates["folder_path"]).strip()
        if "auto_switch" in updates:
            proj.auto_switch = bool(updates["auto_switch"])
        proj.updated_at = time.time()
        self._save_index()
        return proj

    def delete_project(self, project_id: str) -> bool:
        if project_id not in self._projects:
            return False
        del self._projects[project_id]
        if self.active_id == project_id:
            self.active_id = next(iter(self._projects.keys()), "")

        proj_dir = self.projects_dir / project_id
        if proj_dir.is_dir():
            try:
                shutil.rmtree(proj_dir)
            except Exception as exc:
                log.warning("Could not delete project directory %s: %s", proj_dir, exc)

        self._save_index()
        return True

    def reset_all(self, initial_name: str = "General", description: str = "") -> Project:
        """Reset projects to a fresh single project with clean context."""
        self._projects.clear()
        if self.projects_dir.is_dir():
            try:
                shutil.rmtree(self.projects_dir)
            except Exception as exc:
                log.warning("Could not rmtree projects: %s", exc)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        pid = DEFAULT_PROJECT_ID
        now = time.time()
        proj = Project(
            id=pid,
            name=initial_name.strip() or DEFAULT_PROJECT_NAME,
            description=description.strip(),
            created_at=now,
            updated_at=now,
        )
        self._projects[pid] = proj
        self.active_id = pid
        self.get_project_dir(pid)
        self._save_index()
        return proj

    def _meetings_store(self, project_id: str) -> ProjectMeetingsStore:
        return ProjectMeetingsStore(self.get_project_dir(project_id))

    def _titles_by_project(self) -> dict[str, list[str]]:
        return {pid: project_context_titles(self.get_project_dir(pid)) for pid in self._projects}

    def sync_meeting_links(
        self,
        events: list[CalendarEvent],
        settings: Any,
        http: Any | None = None,
        confidence_fn: Any | None = None,
    ) -> None:
        if not events or not self._projects:
            return
        events_by_id = {event.id: event for event in events}
        for store in (self._meetings_store(pid) for pid in self._projects):
            store.refresh_metadata(events_by_id)
        assignments = match_events_with_ai(
            events,
            self.list_projects(),
            self._titles_by_project(),
            settings,
            http=http,
            confidence_fn=confidence_fn,
        )
        for pid in self._projects:
            store = self._meetings_store(pid)
            linked = store.linked_entries()
            excluded = store.excluded_ids()
            per_project: dict[str, CalendarEvent | None] = {}
            for event_id, project_id in assignments.items():
                if event_id in excluded:
                    continue
                if project_id == pid:
                    per_project[event_id] = events_by_id.get(event_id)
                elif project_id is not None and project_id != pid:
                    entry = linked.get(event_id)
                    if entry and str(entry.get("linked_by") or "") == "ai":
                        per_project[event_id] = None
                elif project_id is None:
                    entry = linked.get(event_id)
                    if entry and str(entry.get("linked_by") or "") == "ai":
                        per_project[event_id] = None
            for event_id, entry in linked.items():
                if str(entry.get("linked_by") or "") == "user" and event_id in events_by_id:
                    per_project.setdefault(event_id, events_by_id[event_id])
            store.apply_ai_links(per_project, events_by_id)

    def rescan_meeting_links(
        self,
        project_id: str,
        events: list[CalendarEvent],
        settings: Any,
        http: Any | None = None,
        confidence_fn: Any | None = None,
    ) -> dict[str, Any]:
        self.sync_meeting_links(events, settings, http=http, confidence_fn=confidence_fn)
        return self.meetings_view(project_id)

    def meetings_view(self, project_id: str) -> dict[str, Any]:
        if project_id not in self._projects:
            raise KeyError(f"Project not found: {project_id}")
        return self._meetings_store(project_id).meetings_payload()

    def update_meetings(
        self,
        project_id: str,
        add: list[str],
        remove: list[str],
        events: list[CalendarEvent],
    ) -> dict[str, Any]:
        if project_id not in self._projects:
            raise KeyError(f"Project not found: {project_id}")
        events_by_id = {event.id: event for event in events}
        for eid in add:
            eid = str(eid).strip()
            if not eid:
                continue
            for other_id in self._projects:
                if other_id == project_id:
                    continue
                other = self._meetings_store(other_id)
                if eid in other.linked_entries():
                    other.apply_user_changes([], [eid], events_by_id)
        self._meetings_store(project_id).apply_user_changes(add, remove, events_by_id)
        return self.meetings_view(project_id)

    def linked_events_for_project(self, project_id: str) -> list[dict[str, Any]]:
        if project_id not in self._projects:
            return []
        return self._meetings_store(project_id).meetings_payload()["meetings"]

    def calendar_events_with_projects(self, events: list[CalendarEvent]) -> list[dict[str, Any]]:
        mapping = index_event_project_map(self.projects_dir, list(self._projects.keys()))
        rows = []
        for event in events:
            rows.append(
                {
                    "event_id": event.id,
                    "title": event.title,
                    "start": event.start,
                    "end": event.end,
                    "attendees": list(event.attendees),
                    "project_id": mapping.get(event.id),
                }
            )
        return rows

    def active_upcoming_meetings(self, events: list[CalendarEvent]) -> list[CalendarEvent]:
        if not self.active_id:
            return []
        linked_ids = {row["event_id"] for row in self.linked_events_for_project(self.active_id)}
        if not linked_ids:
            return []
        return [event for event in events if event.id in linked_ids]

    def active_next_meeting(self) -> dict[str, Any] | None:
        if not self.active_id:
            return None
        return self._meetings_store(self.active_id).meetings_payload().get("next")

    def suggested_switch(self, upcoming_events: list[CalendarEvent]) -> dict[str, Any] | None:
        if not upcoming_events or not self.active_id:
            return None
        mapping = index_event_project_map(self.projects_dir, list(self._projects.keys()))
        for event in upcoming_events[:5]:
            matched_id = mapping.get(event.id)
            if not matched_id or matched_id == self.active_id:
                continue
            matched_proj = self.get_project(matched_id)
            if matched_proj is None:
                continue
            suggestion_id = f"{matched_id}:{event.id}"
            if self.dismissed_suggestion_id == suggestion_id:
                continue
            return {
                "suggestion_id": suggestion_id,
                "project_id": matched_id,
                "project_name": matched_proj.name,
                "event_id": event.id,
                "event_title": event.title,
                "event_start": event.start,
            }
        return None

    def dismiss_suggestion(self, suggestion_id: str) -> None:
        self.dismissed_suggestion_id = suggestion_id

    def sync_local_folder(self, project_id: str, context: Any) -> list[str]:
        """Scans the project's folder_path for markdown/text/pdf/docx files and loads them into context."""
        proj = self.get_project(project_id)
        if not proj or not proj.folder_path:
            return []
        folder = Path(proj.folder_path)
        if not folder.is_dir():
            return []

        synced = []
        for file in folder.iterdir():
            if not file.is_file() or file.name.startswith("."):
                continue
            suffix = file.suffix.lower()
            if suffix not in {".md", ".txt", ".markdown", ".pdf", ".docx"}:
                continue
            try:
                data = file.read_bytes()
                rel_path = str(file.name)
                from escort.context.chunk import sections_from_markdown

                text = extract_text(file.name, data)
                if not text.strip():
                    continue
                sections = sections_from_markdown(text)
                existing = next(
                    (it for it in context.items() if it.source_kind == "local_file" and it.external_id == rel_path),
                    None,
                )
                if existing:
                    existing.title = file.stem
                    existing.sections = sections
                    context.add_item(existing)
                else:
                    item = ContextItem(
                        id=new_id("ctx"),
                        source_kind="local_file",
                        source_label="Local File",
                        title=file.stem,
                        sections=sections,
                        enabled=True,
                        external_id=rel_path,
                    )
                    context.add_item(item)
                synced.append(file.name)
            except Exception as exc:
                log.warning("Could not sync file %s: %s", file, exc)

        return synced
