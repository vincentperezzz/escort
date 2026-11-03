from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from escort.context.extract import extract_text, sniff_kind
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

            # Auto-migrate existing single context.json if present at root
            root_context = self.base_dir / "context.json"
            target_context = default_dir / "context.json"
            if root_context.is_file() and not target_context.exists():
                try:
                    shutil.copy2(root_context, target_context)
                    log.info("Migrated existing root context.json to %s", target_context)
                except Exception as exc:
                    log.warning("Could not copy existing context.json: %s", exc)

            root_uploads = self.base_dir / "uploads"
            target_uploads = default_dir / "uploads"
            if root_uploads.is_dir() and not target_uploads.exists():
                try:
                    shutil.copytree(root_uploads, target_uploads)
                    log.info("Migrated existing uploads folder to %s", target_uploads)
                except Exception as exc:
                    log.warning("Could not copy existing uploads: %s", exc)

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

        if not self._projects:
            default_proj = Project(
                id=DEFAULT_PROJECT_ID,
                name=DEFAULT_PROJECT_NAME,
                description=DEFAULT_PROJECT_DESC,
                created_at=time.time(),
                updated_at=time.time(),
            )
            self._projects[default_proj.id] = default_proj

        active = data.get("active_id")
        if active and active in self._projects:
            self.active_id = active
        else:
            self.active_id = next(iter(self._projects.keys()))

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

    def get_active_project(self) -> Project:
        proj = self.get_project(self.active_id)
        if proj is None:
            self.active_id = next(iter(self._projects.keys()))
            proj = self._projects[self.active_id]
        return proj

    def get_project_dir(self, project_id: str) -> Path:
        proj_dir = self.projects_dir / project_id
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    def get_active_project_dir(self) -> Path:
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
            calendar_keywords=[str(k).strip() for k in calendar_keywords or [] if str(k).strip()],
            calendar_domains=[str(d).strip().lower() for d in calendar_domains or [] if str(d).strip()],
            folder_path=folder_path.strip(),
            auto_switch=auto_switch,
            created_at=now,
            updated_at=now,
        )
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
        if "calendar_keywords" in updates and isinstance(updates["calendar_keywords"], list):
            proj.calendar_keywords = [str(k).strip() for k in updates["calendar_keywords"] if str(k).strip()]
        if "calendar_domains" in updates and isinstance(updates["calendar_domains"], list):
            proj.calendar_domains = [str(d).strip().lower() for d in updates["calendar_domains"] if str(d).strip()]
        if "folder_path" in updates:
            proj.folder_path = str(updates["folder_path"]).strip()
        if "auto_switch" in updates:
            proj.auto_switch = bool(updates["auto_switch"])
        proj.updated_at = time.time()
        self._save_index()
        return proj

    def delete_project(self, project_id: str) -> bool:
        if project_id not in self._projects:
            return False
        if len(self._projects) <= 1:
            raise ValueError("Cannot delete the only remaining project.")

        del self._projects[project_id]
        if self.active_id == project_id:
            self.active_id = next(iter(self._projects.keys()))

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

    def match_calendar_event(self, event: CalendarEvent) -> tuple[Project | None, float]:
        """Returns the best matching project and score for an event, or (None, 0.0)."""
        best_proj: Project | None = None
        best_score = 0.0

        event_text = f"{event.title} {event.description}".lower()
        event_attendees = [a.lower() for a in event.attendees]

        for proj in self._projects.values():
            score = 0.0
            # Check keywords against title/description
            for kw in proj.calendar_keywords:
                kw_lower = kw.lower()
                if re.search(rf"\b{re.escape(kw_lower)}\b", event.title.lower()):
                    score += 2.0
                elif kw_lower in event_text:
                    score += 1.0

            # Check attendee email domains
            for domain in proj.calendar_domains:
                domain_lower = domain.lstrip("@").lower()
                for attendee in event_attendees:
                    if attendee.endswith(f"@{domain_lower}") or domain_lower in attendee:
                        score += 2.5

            if score > best_score:
                best_score = score
                best_proj = proj

        return (best_proj, best_score) if best_score >= 1.0 else (None, 0.0)

    def suggested_switch(self, upcoming_events: list[CalendarEvent]) -> dict[str, Any] | None:
        """Suggests a project switch if an upcoming meeting matches a different project."""
        if not upcoming_events:
            return None
        # Check the next event
        for event in upcoming_events[:3]:
            matched_proj, score = self.match_calendar_event(event)
            if matched_proj and matched_proj.id != self.active_id:
                if self.dismissed_suggestion_id == f"{matched_proj.id}:{event.id}":
                    continue
                return {
                    "suggestion_id": f"{matched_proj.id}:{event.id}",
                    "project_id": matched_proj.id,
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
