from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

import httpx

from escort.capture import monitors as monitor_mod
from escort.context.library import ContextLibrary
from escort.context.mock_data import mock_import_item
from escort.context.sources.base import ConnectorError, ConnectorSetupError
from escort.context.sources.google_auth import PROVIDER as GOOGLE, REVOKE_HELP_URL
from escort.context.sources.google_docs import DictNotesState, GoogleNotes, public_doc
from escort.context.sources.notion_source import PROVIDER as NOTION
from escort.context.sources.notion_write import NotionNotes, NotionNotesState, UpdatePlans, public_page, public_target
from escort.context.sources.registry import Connectors
from escort.context.prep_chat import prep_chat
from escort.context.settings_intent import describe_settings, handle_settings_message
from escort.desktop import live_capture
from escort.desktop.devices import list_audio_devices
from escort.desktop.overlay import DEFAULT_OPACITY, clamp_opacity
from escort.desktop.secret_store import MemorySecretStore, SecretStore, open_secret_store
from escort.proactive import ProactiveEngine
from escort.replay import build_pipeline, load_scenario, run_replay
from escort.settings import Settings, load_settings
from escort.store import MeetingStore, new_id, utc_now
from escort.summary import empty_summary, generate_summary, rules_summary

if TYPE_CHECKING:
    from escort.desktop.overlay import OverlayController


class MainWindowController(Protocol):
    def show(self) -> None: ...

    def hide(self) -> None: ...


class OverlayProcess(Protocol):
    """An overlay living in another process (Electron) that follows GET /api/overlay itself."""

    def is_running(self) -> bool: ...

    def stop(self) -> None: ...


NOTE_DESTINATIONS = ("drive", "notion")
NOTION_PLAN_TTL_S = 600.0
IDLE_SCREEN = {"monitoring": False, "last_frame_at": None, "frames_seen": 0, "changed_frames": 0}
MONITOR_CACHE_S = 3.0
PROACTIVE_CONTEXT_S = 60.0  # calendar + notes for the proactive engine are re-read at most this often

log = logging.getLogger("escort.desktop")

KEY_NAMES = (
    "meta_api_key",
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "whatsapp_verify_token",
    "notion_oauth_token",
    "notion_mcp_token",
    "notion_mcp_client",
    "google_oauth_token",
    "google_client_secret",
    "google_api_key",
)


def config_dir() -> Path:
    override = os.environ.get("ESCORT_CONFIG_DIR")
    if override:
        path = Path(override)
    elif os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / "Escort"
    else:
        path = Path.home() / ".config" / "Escort"
    path.mkdir(parents=True, exist_ok=True)
    return path


class DesktopRuntime:
    def __init__(
        self,
        store: MeetingStore,
        secrets: SecretStore | None = None,
        config_path: Path | None = None,
        connectors: Connectors | None = None,
        summary_client: httpx.Client | None = None,
        monitor_lister: Callable[[], list[dict[str, int]]] | None = None,
    ) -> None:
        self.store = store
        self._monitor_lister = monitor_lister
        self._monitor_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._chat_pending: dict[str, Any] | None = None
        self.secrets = secrets or open_secret_store()
        self.config_path = config_path or (config_dir() / "config.json")
        self.listening = False
        self.listening_started_at: str | None = None
        self._lock = threading.RLock()
        self.prefs = self._read_prefs()
        self.overlay: OverlayController | None = None
        self.overlay_process: OverlayProcess | None = None
        self.overlay_visible = False
        self.overlay_collapsed = False
        self.overlay_input_focus = False
        self.main_window: MainWindowController | None = None
        self._main_hidden = False
        self.live: live_capture.LiveSession | None = None
        self.proactive: ProactiveEngine | None = None
        self._proactive_ctx: tuple[float, dict[str, Any]] | None = None
        self.summary: dict[str, Any] = empty_summary()
        self._summary_gen = 0
        self._summary_thread: threading.Thread | None = None
        self._summary_client = summary_client
        self._observers: list[Callable[[], None]] = []
        key = self.secrets.get("meta_api_key")
        if key and not os.environ.get("MODEL_API_KEY"):
            # Keychain survives restarts; Muse clients read the key from the environment.
            os.environ["MODEL_API_KEY"] = key
        self.connectors = connectors or Connectors(
            self.secrets,
            client_id_getter=lambda: (str(self.prefs.get("google_client_id") or "").strip() or None),
        )
        self.connectors.save_notes_getter = lambda: self.save_notes_enabled
        self.connectors.google.on_connected = self.show_main_window
        self.connectors.notion_mcp.on_connected = self.show_main_window
        self.notes = GoogleNotes(self.connectors.google, DictNotesState(self.prefs, on_change=self._write_prefs))
        self.notion_notes = NotionNotes(self.connectors.notion, NotionNotesState(self.prefs, on_change=self._write_prefs), mcp=self.connectors.notion_mcp)
        self.connectors.notion_notes_getter = self._notion_notes_view
        self._notion_plans: dict[str, dict[str, Any]] = {}  # confirmation previews waiting for Confirm / Cancel
        # One Google Doc per meeting; a new id when a new listening session starts or the meeting is deleted.
        self.meeting_id = new_id("mtg")
        self._pending_session: dict[str, Any] | None = None
        self._listen_project_id: str | None = None
        from escort.context.project import ProjectManager
        from escort.context.sources.registry import CalendarSource

        self.projects = ProjectManager(store.media_dir.parent)
        self.context = ContextLibrary(self.projects.get_active_project_dir(), self.secrets.get, connectors=self.connectors)
        calendar_source = self.context.sources.get("google_calendar")
        if isinstance(calendar_source, CalendarSource):
            calendar_source.on_events_fetched = self._on_calendar_events_fetched

    def _context_root(self) -> Path:
        return self.store.media_dir.parent

    def _read_prefs(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"mic_id": "", "loopback_id": "", "whatsapp_phone_number_id": "", "whatsapp_allowed_numbers": "", "whatsapp_graph_version": "v22.0"}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        for secret in KEY_NAMES:
            data.pop(secret, None)
        return data

    def _write_prefs(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        safe = {key: value for key, value in self.prefs.items() if key not in KEY_NAMES and "key" not in key and "token" not in key and "secret" not in key}
        self.config_path.write_text(json.dumps(safe, indent=2), encoding="utf-8")

    def _settings(self) -> Settings:
        settings = load_settings()
        key = self.secrets.get("meta_api_key") or settings.api_key
        return dataclasses.replace(settings, api_key=key or None)

    def screen_status(self) -> dict[str, Any]:
        live = self.live
        screen = live.screen() if live is not None else dict(IDLE_SCREEN)
        # The loop reports what it is grabbing; before its first tick (or when idle) say what it will grab.
        return {**screen, "monitor": screen.get("monitor") or self._watch_target()["label"]}

    # --- watched display -------------------------------------------------------

    def list_monitors(self, fresh: bool = False) -> list[dict[str, Any]]:
        cached = self._monitor_cache
        if not fresh and cached is not None and time.monotonic() - cached[0] < MONITOR_CACHE_S:
            return cached[1]
        found = monitor_mod.list_monitors(self._monitor_lister or monitor_mod.enumerate_raw)
        self._monitor_cache = (time.monotonic(), found)
        return found

    @property
    def watch_choice(self) -> Any:
        try:
            return monitor_mod.normalize_choice(self.prefs.get("watch_monitor", monitor_mod.PRIMARY))
        except ValueError:
            return monitor_mod.PRIMARY

    def _watch_target(self) -> dict[str, Any]:
        """Polled by the screen loop every tick, so a new choice applies live."""
        return monitor_mod.resolve(self.watch_choice, self.list_monitors())

    def monitors_status(self, fresh: bool = False) -> dict[str, Any]:
        monitors = self.list_monitors(fresh=fresh)
        effective = monitor_mod.resolve(self.watch_choice, monitors)
        return {
            "monitors": monitors,
            "selected": self.watch_choice,
            "effective": effective,
            "notice": effective["notice"],
            "overlay_on_watched": self._overlay_on_watched(effective, monitors),
        }

    def set_watch_monitor(self, selected: Any) -> dict[str, Any]:
        """Persist the watched display; a running screen loop picks it up on its next tick.

        Raises ValueError for anything that is not 'primary', 'all' or a connected display.
        """
        choice = monitor_mod.normalize_choice(selected)
        monitors = self.list_monitors(fresh=True)
        if isinstance(choice, int) and monitors and not any(item["index"] == choice for item in monitors):
            count = len(monitors)
            raise ValueError(f"There is no Display {choice}; this machine has {count} display{'s' if count != 1 else ''}.")
        with self._lock:
            if self.prefs.get("watch_monitor") != choice:
                self.prefs["watch_monitor"] = choice
                self._write_prefs()
        self._notify()
        return self.monitors_status()

    def _overlay_on_watched(self, effective: dict[str, Any], monitors: list[dict[str, Any]]) -> bool | None:
        """True when the overlay window overlaps the watched display; None when unknown."""
        getter = getattr(self.overlay, "window_rect", None)
        if getter is None or not self.overlay_visible:
            return None
        try:
            rect = getter()
        except Exception:
            return None
        if rect is None:
            return None
        if effective["index"] == monitor_mod.ALL_INDEX:
            return True
        watched = next((item for item in monitors if item["index"] == effective["index"]), None)
        return monitor_mod.rects_overlap(rect, watched) if watched else None

    def capture_error(self) -> str | None:
        live = self.live
        return live.error() if live is not None else None

    def _on_calendar_events_fetched(self, events: list[Any]) -> None:
        with self._lock:
            self.projects.sync_meeting_links(events, self._settings())

    def _synced_calendar_events(self, limit: int = 25) -> list[Any]:
        from escort.context.models import CalendarEvent

        return self.context.upcoming_events(limit)

    def public_status(self) -> dict[str, Any]:
        snapshot = self.store.snapshot()
        active_project = self.projects.get_active_project()
        all_projects = self.projects.list_projects()
        all_upcoming = self._synced_calendar_events(25)
        scoped_upcoming = self.projects.active_upcoming_meetings(all_upcoming)[:6]
        suggestion = self.projects.suggested_switch(all_upcoming)
        return {
            "active_project": active_project.to_dict() if active_project else None,
            "active_project_next_meeting": self.projects.active_next_meeting(),
            "projects": [p.to_dict() for p in all_projects],
            "needs_project": self.projects.needs_project(),
            "suggested_project": suggestion,
            "listening": self.listening,
            "listening_started_at": self.listening_started_at,
            "screen": self.screen_status(),
            "capture_error": self.capture_error(),
            "summary": self.summary_status(),
            "has_meta_key": bool(self.secrets.get("meta_api_key")),
            "has_whatsapp_token": bool(self.secrets.get("whatsapp_access_token")),
            "prefs": {
                "mic_id": self.prefs.get("mic_id", ""),
                "loopback_id": self.prefs.get("loopback_id", ""),
                "whatsapp_phone_number_id": self.prefs.get("whatsapp_phone_number_id", ""),
                "whatsapp_allowed_numbers": self.prefs.get("whatsapp_allowed_numbers", ""),
                "whatsapp_graph_version": self.prefs.get("whatsapp_graph_version", "v22.0"),
                "google_client_id": self.prefs.get("google_client_id", ""),
                "watch_monitor": self.watch_choice,
            },
            "monitors": self.monitors_status(),
            "devices": list_audio_devices(),
            "meeting": snapshot,
            "context": self.context.public_view(active_project, all_projects, upcoming=scoped_upcoming),
            "connectors": self.connectors.status(),
            "notes_drive": self.notes_drive_status(),
            "notes": self.notes_status(),
            "overlay": self.overlay_status(),
            "sessions": self._status_sessions(active_project),
            "mcp":"http://127.0.0.1:8747/mcp",
            "whatsapp": "http://127.0.0.1:8748/webhook",
        }

    def _status_sessions(self, active_project: Any | None) -> list[dict[str, Any]]:
        if active_project is None:
            return []
        try:
            return self.projects.list_sessions(active_project.id)[:12]
        except KeyError:
            return []

    def save_setup(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if body.get("meta_api_key"):
                clean_key = str(body["meta_api_key"]).strip()
                if clean_key:
                    self.secrets.set("meta_api_key", clean_key)
                    os.environ["MODEL_API_KEY"] = clean_key
            for name in ("whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token", "notion_oauth_token", "google_oauth_token", "google_client_secret", "google_api_key"):
                if body.get(name):
                    value = str(body[name])
                    self.secrets.set(name, value.strip() if name in ("google_client_secret", "google_api_key") else value)
            for key in ("mic_id", "loopback_id", "whatsapp_phone_number_id", "whatsapp_allowed_numbers", "whatsapp_graph_version", "google_client_id"):
                if key in body:
                    self.prefs[key] = body[key].strip() if key == "google_client_id" and isinstance(body[key], str) else body[key]
            self._write_prefs()
        return self.public_status()

    def set_active_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            self.projects.set_active_project(project_id)
            self.context.switch_root(self.projects.get_active_project_dir())
            self.context.apply_to_store(self.store)
            if self.listening:
                # Switched mid-call: the recap is filed under the new project, and the card brain reads its notes.
                self._listen_project_id = project_id
                self._proactive_ctx = None
                pipeline = getattr(self.live, "pipeline", None)
                if pipeline is not None:
                    pipeline.prd_text = self.context.combined_markdown()
        return self.public_status()

    def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            proj = self.projects.create_project(
                name=str(body.get("name") or "New Project"),
                description=str(body.get("description") or ""),
                calendar_keywords=body.get("calendar_keywords"),
                calendar_domains=body.get("calendar_domains"),
                folder_path=str(body.get("folder_path") or ""),
                auto_switch=bool(body.get("auto_switch", True)),
            )
            if body.get("activate") or self.projects.get_active_project() is None:
                self.projects.set_active_project(proj.id)
                self.context.switch_root(self.projects.get_active_project_dir())
                self.context.apply_to_store(self.store)
            events = self._synced_calendar_events(25)
            if events:
                self.projects.sync_meeting_links(events, self._settings())
        return self.public_status()

    def get_project_meetings(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return self.projects.meetings_view(project_id)

    def put_project_meetings(self, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        add = [str(x) for x in body.get("add") or []]
        remove = [str(x) for x in body.get("remove") or []]
        with self._lock:
            events = self._synced_calendar_events(25)
            return self.projects.update_meetings(project_id, add, remove, events)

    def rescan_project_meetings(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            events = self._synced_calendar_events(25)
            return self.projects.rescan_meeting_links(project_id, events, self._settings())

    def calendar_events_public(self) -> dict[str, Any]:
        with self._lock:
            events = self._synced_calendar_events(25)
            return {"events": self.projects.calendar_events_with_projects(events)}

    def update_project(self, project_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            proj = self.projects.update_project(project_id, updates)
            if updates.get("folder_path"):
                self.projects.sync_local_folder(project_id, self.context)
                self.context.apply_to_store(self.store)
        return {"project": proj.to_dict()}

    def delete_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            deleted = self.projects.delete_project_cascade(project_id)
            if deleted is None:
                raise KeyError(f"Project not found: {project_id}")
            self._clear_project_destinations(project_id)
            self.context.switch_root(self.projects.get_active_project_dir())
            self.context.apply_to_store(self.store)
        return {"ok": True, "deleted": deleted}

    def _clear_project_destinations(self, project_id: str) -> None:
        scoped = self.prefs.get("project_destinations")
        if isinstance(scoped, dict) and project_id in scoped:
            scoped = dict(scoped)
            scoped.pop(project_id, None)
            self.prefs["project_destinations"] = scoped
            self._write_prefs()

    def _remember_project_destinations(self) -> None:
        active = self.projects.get_active_project()
        if active is None:
            return
        scoped = dict(self.prefs.get("project_destinations") or {})
        scoped[active.id] = {
            "notes_destination": self.prefs.get("notes_destination"),
            "notion_notes_parent": self.notion_notes.state.parent(),
            "notion_notes_targets": self.notion_notes.state.targets(),
            "google_notes_folder_id": self.notes.state.folder_id(),
            "google_notes_docs": self.notes.state.docs(),
            "notion_save_notes": self.prefs.get("notion_save_notes"),
            "google_save_notes": self.prefs.get("google_save_notes"),
        }
        self.prefs["project_destinations"] = scoped
        self._write_prefs()

    def list_sessions(self, project_id: str | None) -> dict[str, Any]:
        with self._lock:
            pid = project_id or self.projects.active_id
            if not pid:
                return {"sessions": []}
            self.projects.require_project(pid)
            rows = self.projects.list_sessions(pid)
        return {"sessions": rows}

    def session_recap(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            for proj in self.projects.list_projects():
                payload = self.projects.session_store(proj.id).recap_payload(session_id)
                if payload is not None:
                    return payload
        raise KeyError(f"Session not found: {session_id}")

    def session_media(self, session_id: str, name: str) -> Path | None:
        with self._lock:
            for proj in self.projects.list_projects():
                try:
                    found = self.projects.session_store(proj.id).resolve_media(session_id, name)
                except KeyError:
                    return None
                if found is not None:
                    return found
        return None

    def delete_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            for proj in self.projects.list_projects():
                store = self.projects.session_store(proj.id)
                if store.delete(session_id):
                    return {"ok": True}
        raise KeyError(f"Session not found: {session_id}")

    def clear_chat(self) -> dict[str, Any]:
        with self._lock:
            self.context.clear_chat()
        return self.public_status()

    def sync_project_folder(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            synced = self.projects.sync_local_folder(project_id, self.context)
            if synced:
                self.context.apply_to_store(self.store)
        return {"synced": synced, "status": self.public_status()}

    def dismiss_project_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        self.projects.dismiss_suggestion(suggestion_id)
        return self.public_status()

    def reset_projects(self, name: str = "General", description: str = "") -> dict[str, Any]:
        with self._lock:
            self.projects.reset_all(name, description)
            self.context.switch_root(self.projects.get_active_project_dir())
            self.context.apply_to_store(self.store)
        return self.public_status()

    def set_prd(self, text: str, name: str = "uploaded.md") -> dict[str, Any]:
        self.context.add_paste(text, title=name)
        self.context.apply_to_store(self.store)
        return self.public_status()

    def context_upload(self, filename: str, data: bytes) -> dict[str, Any]:
        with self._lock:
            self.context.add_upload(filename, data)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_remove(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            self.context.remove_item(item_id)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_import(self, source: str, external_id: str) -> dict[str, Any]:
        with self._lock:
            self.context.import_external(source, external_id)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def context_search(self, source: str, query: str) -> dict[str, Any]:
        return {"hits": self.context.search_connector(source, query)}

    def context_match(self, event_id: str) -> dict[str, Any]:
        event, proposals = self.context.propose_matches(event_id)
        return {
            "event": event.to_dict() if event else None,
            "proposals": [item.to_dict() for item in proposals],
        }

    def context_prep(self, event_id: str, item_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            self.context.confirm_prep(event_id, item_ids)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def _meeting_chat_context(self) -> tuple[str, str | None]:
        """Assemble live transcript, partials, cards, calendar attendees, and latest screen frame for Muse chat."""
        lines: list[str] = []
        screen = self.screen_status()
        target = self._watch_target()
        enabled_items = [item for item in self.context.items() if item.enabled]
        if enabled_items:
            item_list = ", ".join(f"{item.source_label} · {item.title}" for item in enabled_items)
            lines.append(f"Loaded context documents ({len(enabled_items)}): {item_list}")
        else:
            lines.append(
                "Loaded context documents: none loaded prior to this meeting — operating in live note-taking & "
                "intra-meeting alignment mode (capturing live notes from the conversation and screen, and flagging "
                "whenever later parts of the conversation do not align with what was stated or agreed in the first part of the meeting)."
            )
        lines.append(
            f"Live listening status: {'ON (actively capturing mic, system audio, and screen)' if self.listening else 'OFF (not currently capturing live audio)'} "
            f"| Watched display: {screen.get('monitor') or target.get('label') or 'Display 1'}"
        )
        try:
            all_upcoming = self.context.upcoming_events(25)
            events = self.projects.active_upcoming_meetings(all_upcoming)[:5]
        except Exception:
            events = []
        prep_id = (self.context.data.get("prep") or {}).get("selected_event_id")
        if events:
            cal_lines = []
            for ev in events[:4]:
                marker = " [SELECTED MEETING]" if ev.id == prep_id else ""
                attendees = f" | Attendees: {', '.join(ev.attendees)}" if ev.attendees else ""
                cal_lines.append(f"- {ev.title} ({ev.start}){marker}{attendees}")
            lines.append("Upcoming calendar meetings:\n" + "\n".join(cal_lines))

        frame = live_capture.grab_chat_frame(self.store, monitor_index=int(target.get("index", 1)))
        with self.store.lock:
            all_utterances = list(self.store.utterances)
            partials = dict(self.store.partials)
            cards = list(self.store.cards[-15:])
            if frame is None:
                frame = self.store.latest_frame()

        if cards:
            card_lines = []
            for card in cards:
                extra = f" (Section: {card.prd_section})" if card.prd_section else ""
                who_what = f" [{card.who}: {card.what}]" if card.who or card.what else ""
                card_lines.append(f"- [{card.type}] {card.title}{extra}{who_what}: {card.body}")
            lines.append("Live cards & flags raised in this meeting:\n" + "\n".join(card_lines))

        if all_utterances or partials:
            turn_lines = []
            if len(all_utterances) > 40:
                turn_lines.append("[First part of the meeting (baseline statements & agreements)]")
                for utt in all_utterances[:12]:
                    spk = f"{utt.speaker} [Speaker {utt.vendor_speaker}]" if utt.vendor_speaker else utt.speaker
                    turn_lines.append(f"{spk}: {utt.text}")
                turn_lines.append(f"... ({len(all_utterances) - 40} intermediate turns omitted) ...")
                turn_lines.append("[Recent part of the meeting]")
                recent_utterances = all_utterances[-28:]
            else:
                recent_utterances = all_utterances
            for utt in recent_utterances:
                spk = f"{utt.speaker} [Speaker {utt.vendor_speaker}]" if utt.vendor_speaker else utt.speaker
                turn_lines.append(f"{spk}: {utt.text}")
            for spk, part_text in partials.items():
                if part_text and part_text.strip():
                    turn_lines.append(f"{spk} (speaking right now): {part_text.strip()}")
            lines.append("Live meeting transcript (Me = user's mic, Them = remote call audio):\n" + "\n".join(turn_lines))
        else:
            lines.append("Live meeting transcript: (No speech transcribed yet in this session.)")

        frame_path = frame.path if frame is not None else None
        if frame_path:
            lines.append("Latest meeting screen frame: attached as an image. Use it to read participant names, active-speaker indicators, open mics, slides, and shared documents.")
        return "\n\n".join(lines), frame_path

    def context_chat(self, message: str) -> dict[str, Any]:
        """Prep chat and overlay ask box. Settings requests are handled here, before Muse."""
        with self._lock:
            pending, self._chat_pending = self._chat_pending, None
            self.context.append_chat("user", message)
            settings_reply = handle_settings_message(message, self, pending)
            changed: list[dict[str, Any]] = []
            if settings_reply is not None:
                answer = settings_reply.reply
                changed = settings_reply.changed
                self._chat_pending = settings_reply.pending
            else:
                meeting_note, frame_path = self._meeting_chat_context()
                answer = prep_chat(
                    message,
                    self.context.combined_markdown(),
                    self.context.chat_history(),
                    settings_note=describe_settings(self),
                    settings=self._settings(),
                    meeting_note=meeting_note,
                    frame_path=frame_path,
                )
            self.context.append_chat("assistant", answer, changed=changed or None)
        return {"reply": answer, "changed": changed, "context": self.context.public_view()}

    def load_demo(self) -> dict[str, Any]:
        scenario = load_scenario()
        settings = load_settings()
        self.meeting_id = new_id("mtg")
        self.context.clear()
        self.context.add_paste(scenario.prd_text, title="BayaniPay scope (demo)")
        # The demo is a fixed scenario: always use the bundled sample docs, never live accounts.
        self.context.add_item(mock_import_item("notion", "notion_phase_scope"))
        self.context.add_item(mock_import_item("google_docs", "docs_sow"))
        self.context.confirm_prep("evt_bayani_scope", [item.id for item in self.context.items()[:2]])
        self.context.apply_to_store(self.store)
        pipeline = build_pipeline(
            self.store,
            scenario,
            settings,
            transcriber_mode="scripted",
            spark_mode="rules",
            segmenter_mode="fallback",
        )
        self.context.apply_to_store(self.store)
        run_replay(pipeline, scenario, pace_ms=0)
        return self.public_status()

    def connectors_status(self) -> dict[str, Any]:
        return self.connectors.status()

    def connect_google(self) -> dict[str, str]:
        return self.connectors.connect_google()

    def connect_notion(self, token: str = "") -> dict[str, Any]:
        """No token: one-click OAuth (returns auth_url; the UI polls status). A token or callback URL: REST fallback or exchange."""
        raw = token.strip()
        if raw:
            if "code=" in raw or raw.startswith("http://") or raw.startswith("https://"):
                try:
                    import urllib.parse

                    parsed = urllib.parse.urlsplit(raw)
                    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}
                    code = params.get("code") or (raw.split("code=")[1].split("&")[0] if "code=" in raw else "")
                    if code:
                        flow = self.connectors.notion_mcp._flow
                        if flow is not None:
                            self.connectors.notion_mcp.exchange_code(code, flow)
                            self._notify()
                            return self.connectors_status()
                except Exception as exc:
                    log.warning("Manual code exchange failed: %s", exc)
            return self.connectors.connect_notion(raw)
        out = self.connectors.connect_notion_oauth()
        self._notify()
        return out

    def disconnect_connector(self, provider: str) -> dict[str, Any]:
        return self.connectors.disconnect(provider)

    # --- Save notes to Drive (user-clicked writes only) ------------------------

    @property
    def save_notes_enabled(self) -> bool:
        return bool(self.prefs.get("google_save_notes", False))

    def set_save_notes(self, enabled: bool) -> dict[str, Any]:
        """Turn note saving on/off. On without the scopes starts the incremental consent flow."""
        with self._lock:
            if self.save_notes_enabled != enabled:
                self.prefs["google_save_notes"] = enabled
                self._write_prefs()
        out: dict[str, Any] = {}
        google = self.connectors.google
        if enabled and google.connected() and not google.can_write_notes() and google.pending_scopes() is None:
            out = self.connectors.connect_google(notes=True)
        if not enabled:
            out["revoke_url"] = REVOKE_HELP_URL
        self._notify()
        return {**self.connectors.status(), **out}

    def google_notes_consent(self, capability: str = "notes") -> dict[str, Any]:
        """'Allow note saving' (default) or 'Allow Sheets/Slides access', 'Allow Drive file picking':
        incremental consent for that capability's scopes only."""
        if not self.connectors.google.connected():
            hint = "Turn on Save notes to Drive." if capability == "notes" else "Then try again."
            raise ConnectorSetupError(GOOGLE, f"Connect Google first. {hint}", code="not_connected")
        return self.connectors.connect_google(capability=capability)

    def google_picker_config(self) -> dict[str, Any]:
        return self.connectors.picker_config()

    def context_drive_link(self, link: str) -> dict[str, Any]:
        return self.connectors.resolve_drive_link(link)

    def context_import_drive_file(self, file_id: str, mime_type: str, name: str) -> dict[str, Any]:
        with self._lock:
            self.context.import_drive_file(file_id, mime_type, name)
            self.context.apply_to_store(self.store)
        return self.public_status()

    def meeting_title(self) -> str:
        title = (self.store.title or "").strip()
        if title and title != "Live meeting":
            return title
        summary_title = str(self.summary.get("title") or "").strip()
        return summary_title or title or "Meeting"

    @property
    def notion_save_notes_enabled(self) -> bool:
        return bool(self.prefs.get("notion_save_notes", False))

    def set_notion_notes(self, enabled: bool, parent_id: str | None = None, parent_title: str | None = None) -> dict[str, Any]:
        """Turn 'Save notes to Notion' on/off and (optionally) pick the parent page. No network."""
        with self._lock:
            self.prefs["notion_save_notes"] = enabled
            if parent_id:
                self.prefs["notion_needs_capabilities"] = False
                self.notion_notes.state.set_parent(parent_id, parent_title)
            self._remember_project_destinations()
            self._write_prefs()
        self._notify()
        return self.connectors.status()

    def _notion_notes_view(self) -> dict[str, Any]:
        return {
            "save_notes": self.notion_save_notes_enabled,
            "parent": self.notion_notes.state.parent(),
            "targets": self.notion_targets(),
            "needs_capabilities": bool(self.prefs.get("notion_needs_capabilities")),
        }

    # --- Update an existing Notion page (user-picked targets; preview, then Confirm) ----------

    def notion_targets(self) -> list[dict[str, Any]]:
        entries = sorted(self.notion_notes.state.targets().values(), key=lambda item: str(item.get("picked_at") or ""))
        return [public_target(entry) for entry in entries]

    def add_notion_target(self, page_id: str, title: str | None = None) -> dict[str, Any]:
        """'Choose a page to keep updated': remember a page the user picked from the Notion search."""
        if not page_id.strip():
            raise ConnectorSetupError(NOTION, "Pick a page first.", code="bad_request")
        with self._lock:
            self.notion_notes.state.add_target(page_id.strip(), title)
        self._notify()
        return self.connectors.status()

    def remove_notion_target(self, page_id: str) -> dict[str, Any]:
        with self._lock:
            self.notion_notes.state.remove_target(page_id)
        self._notify()
        return self.connectors.status()

    def notion_find_targets(self, text: str) -> list[dict[str, Any]]:
        """Picked pages whose title the text mentions (whole title, or every significant word of it)."""
        said = " ".join("".join(ch if ch.isalnum() else " " for ch in text.lower()).split())
        found = []
        for target in self.notion_targets():
            words = " ".join("".join(ch if ch.isalnum() else " " for ch in str(target["title"]).lower()).split())
            significant = [word for word in words.split() if len(word) > 2]
            if words and (f" {words} " in f" {said} " or (significant and all(f" {word} " in f" {said} " for word in significant))):
                found.append(target)
        return found

    def _notion_call(self, work: Callable[[], Any]) -> Any:
        try:
            result = work()
        except ConnectorError as err:
            if err.code == "needs_capabilities":
                self.prefs["notion_needs_capabilities"] = True
                self._write_prefs()
            raise
        if self.prefs.get("notion_needs_capabilities"):
            self.prefs["notion_needs_capabilities"] = False
            self._write_prefs()
        return result

    def notion_update_preview(
        self, page_id: str, kind: str, card_id: str | None = None, block_id: str | None = None, text: str | None = None
    ) -> dict[str, Any]:
        """Read the page and describe exactly what would change. Nothing is written until Confirm."""
        if kind not in ("commitment", "summary", "edit"):
            raise ConnectorSetupError(NOTION, "kind must be 'commitment', 'summary' or 'edit'", code="bad_request")
        plans = UpdatePlans(self.notion_notes)
        if kind == "commitment":
            card = next((item for item in self.store.cards if item.id == card_id), None)
            if card is None or card.type != "COMMITMENT":
                raise ConnectorSetupError(NOTION, "That commitment card is not in this meeting.", code="unknown_card")
            build = lambda: plans.commitment(page_id, card.who or "Someone", card.what or card.title, card.when)  # noqa: E731
        elif kind == "summary":
            summary = self.summary_status()
            if summary.get("status") != "ready":
                raise ConnectorSetupError(NOTION, "There is no finished summary to save yet.", code="no_summary")
            build = lambda: plans.summary(page_id, summary)  # noqa: E731
        else:
            if not block_id or not (text or "").strip():
                raise ConnectorSetupError(NOTION, "Say which block and what it should say.", code="bad_request")
            build = lambda: plans.edit(page_id, block_id, str(text).strip())  # noqa: E731
        plan = self._notion_call(build)
        now = time.monotonic()
        with self._lock:
            self._notion_plans = {key: value for key, value in self._notion_plans.items() if now - value["at"] < NOTION_PLAN_TTL_S}
            plan_id = new_id("upd")
            self._notion_plans[plan_id] = {"plan": plan, "at": now, "kind": kind, "card_id": card_id}
        return {"plan_id": plan_id, "kind": kind, "card_id": card_id, "page": plan["page"], "summary": plan["summary"], "lines": plan["lines"]}

    def notion_update_confirm(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            held = self._notion_plans.pop(plan_id, None)
        if held is None or time.monotonic() - held["at"] >= NOTION_PLAN_TTL_S:
            raise ConnectorSetupError(NOTION, "That update is no longer waiting. Start it again.", code="plan_expired")
        page = self._notion_call(lambda: UpdatePlans(self.notion_notes).apply(held["plan"]))
        with self._lock:
            self.prefs["notes_destination"] = "notion"
            self._write_prefs()
        log.info("Updated an existing Notion page after confirmation")
        self._notify()
        return {
            "ok": True,
            "destination": "notion",
            "kind": held["kind"],
            "updated": True,
            "page": page,
            "noted_cards": [held["card_id"]] if held.get("card_id") else [],
        }

    def notion_update_cancel(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            self._notion_plans.pop(plan_id, None)
        return {"ok": True}

    def notes_drive_status(self) -> dict[str, Any]:
        """The current meeting's Google Doc (if any) and whether saving is possible. No network."""
        google = self.connectors.google
        entry = self.notes.doc_for(self.meeting_id)
        return {
            "enabled": self.save_notes_enabled,
            "connected": google.connected(),
            "write_notes": google.can_write_notes(),
            "needs_consent": self.save_notes_enabled and google.connected() and not google.can_write_notes(),
            "meeting_id": self.meeting_id,
            "doc": public_doc(entry),
            "noted_cards": list((entry or {}).get("noted_cards") or []),
            "summary_saved_at": (entry or {}).get("summary_saved_at"),
        }

    def notes_notion_status(self) -> dict[str, Any]:
        """The current meeting's Notion page (if any) and whether saving is possible. No network."""
        connected = self.notion_notes.connected()
        view = self._notion_notes_view()
        entry = self.notion_notes.page_for(self.meeting_id)
        return {
            "enabled": view["save_notes"],
            "connected": connected,
            "write_notes": connected and bool(view["parent"]) and not view["needs_capabilities"],
            "needs_parent": view["save_notes"] and connected and not view["parent"],
            "needs_capabilities": view["needs_capabilities"],
            "parent": view["parent"],
            "targets": view["targets"],
            "meeting_id": self.meeting_id,
            "doc": public_page(entry),
            "noted_cards": list((entry or {}).get("noted_cards") or []),
            "summary_saved_at": (entry or {}).get("summary_saved_at"),
        }

    def notes_status(self) -> dict[str, Any]:
        """Both destinations plus the remembered choice, for the UI's destination picker."""
        enabled = [dest for dest in NOTE_DESTINATIONS if self._dest_enabled(dest)]
        return {"enabled": enabled, "last": self.preferred_destination(), "drive": self.notes_drive_status(), "notion": self.notes_notion_status()}

    def _dest_enabled(self, dest: str) -> bool:
        return self.save_notes_enabled if dest == "drive" else self.notion_save_notes_enabled

    def preferred_destination(self) -> str:
        last = self.prefs.get("notes_destination")
        if last in NOTE_DESTINATIONS and self._dest_enabled(last):
            return last
        return next((dest for dest in NOTE_DESTINATIONS if self._dest_enabled(dest)), "drive")

    def save_notes_drive(self, kind: str, card_id: str | None = None) -> dict[str, Any]:
        """POST /api/notes/drive: append a commitment or the structured summary to this meeting's Google Doc."""
        return self.save_notes("drive", kind, card_id)

    def save_notes_notion(self, kind: str, card_id: str | None = None) -> dict[str, Any]:
        """POST /api/notes/notion: the same, to this meeting's Notion page."""
        return self.save_notes("notion", kind, card_id)

    def save_notes(self, dest: str, kind: str, card_id: str | None = None) -> dict[str, Any]:
        provider = GOOGLE if dest == "drive" else NOTION
        if kind not in ("commitment", "summary"):
            raise ConnectorSetupError(provider, "kind must be 'commitment' or 'summary'", code="bad_request")
        if not self._dest_enabled(dest):
            hint = (
                "Turn on Save notes to Drive to keep this in Google Docs."
                if dest == "drive"
                else "Turn on Save notes to Notion to keep this in Notion."
            )
            raise ConnectorSetupError(provider, hint, code="notes_off")
        writer = self.notes if dest == "drive" else self.notion_notes
        meeting_id, title = self.meeting_id, self.meeting_title()
        try:
            if kind == "commitment":
                card = next((item for item in self.store.cards if item.id == card_id), None)
                if card is None or card.type != "COMMITMENT":
                    raise ConnectorSetupError(provider, "That commitment card is not in this meeting.", code="unknown_card")
                entry = writer.note_commitment(meeting_id, title, card.who or "Someone", card.what or card.title, card.when)
                noted = sorted({*(entry.get("noted_cards") or []), card.id})
                entry = self._update_note_entry(dest, meeting_id, noted_cards=noted)
            else:
                summary = self.summary_status()
                if summary.get("status") != "ready":
                    raise ConnectorSetupError(provider, "There is no finished summary to save yet.", code="no_summary")
                writer.save_summary(meeting_id, title, summary)
                entry = self._update_note_entry(dest, meeting_id, summary_saved_at=utc_now())
        except ConnectorError as err:
            if dest == "notion" and err.code == "needs_capabilities":
                self.prefs["notion_needs_capabilities"] = True
                self._write_prefs()
            raise
        with self._lock:
            self.prefs["notes_destination"] = dest  # remembered for the next Note it / Save summary
            if dest == "notion":
                self.prefs["notion_needs_capabilities"] = False
            self._write_prefs()
        log.info("Saved a %s to the meeting's %s", kind, "Google Doc" if dest == "drive" else "Notion page")
        self._notify()
        public = public_doc if dest == "drive" else public_page
        return {
            "ok": True,
            "destination": dest,
            "kind": kind,
            "doc": public(entry),
            "noted_cards": list(entry.get("noted_cards") or []),
            "summary_saved_at": entry.get("summary_saved_at"),
        }

    def save_notes_preferred(self, kind: str) -> dict[str, Any]:
        """Chat: save to the remembered destination (after the user confirmed)."""
        return self.save_notes(self.preferred_destination(), kind)

    def notes_save_status(self) -> dict[str, Any]:
        """Chat: what saving to the preferred destination would need."""
        dest = self.preferred_destination()
        status = self.notes_drive_status() if dest == "drive" else self.notes_notion_status()
        blocked = None
        if dest == "drive" and status["needs_consent"]:
            blocked = "Google hasn't allowed note saving yet. Open Connections > Google and click Allow note saving."
        if dest == "notion" and (status["needs_parent"] or status["needs_capabilities"]):
            blocked = "Notion isn't ready to save notes yet. Open Connections > Notion to pick a parent page and check the integration's capabilities."
        return {
            **status,
            "destination": dest,
            "label": "Google Docs" if dest == "drive" else "Notion",
            "enabled": any(self._dest_enabled(item) for item in NOTE_DESTINATIONS),
            "blocked": blocked,
        }

    def _update_note_entry(self, dest: str, meeting_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            if dest == "drive":
                docs = self.notes.state.docs()
                entry = {**docs.get(meeting_id, {}), **fields}
                docs[meeting_id] = entry
                self.notes.state.set_docs(docs)
            else:
                pages = self.notion_notes.state.pages()
                entry = {**pages.get(meeting_id, {}), **fields}
                pages[meeting_id] = entry
                self.notion_notes.state.set_pages(pages)
        return entry

    def set_listening(self, on: bool) -> dict[str, Any]:
        with self._lock:
            was = self.listening
            self.listening = on
            if on and self.store.mode in {"idle", "deleted"}:
                self.store.mode = "live"
            if not on and self.store.mode == "live":
                self.store.mode = "idle"
            if on and not was:
                self.meeting_id = new_id("mtg")
                self.listening_started_at = utc_now()
                active = self.projects.get_active_project()
                self._listen_project_id = active.id if active else None
                self._pending_session = None
                self._summary_gen += 1
                self.summary = empty_summary()
                self._start_capture()
                self._start_proactive()
            elif not on and was:
                ended = utc_now()
                started = self.listening_started_at or self.store.started_at
                self._pending_session = {
                    "project_id": self._listen_project_id,
                    "started_at": started,
                    "ended_at": ended,
                    "title": self.meeting_title(),
                }
                self.listening_started_at = None
                self._stop_proactive()
                self._begin_summary(stop_session=self.live)
        self._set_overlay_visible(on, bring_main=not on)
        self._notify()
        return self.public_status()

    def add_jot(self, text: str) -> dict[str, Any]:
        if not text.strip():
            return {"ok": False, "error": "empty note"}
        jot = self.store.add_jot(text)
        self._notify()
        return {"ok": True, "jot": jot}

    def restart_capture(self) -> dict[str, Any]:
        """Restart screen + audio capture inside the same live meeting: turns, cards and notes stay."""
        with self._lock:
            if not self.listening:
                return self.public_status()
            old = self.live
            self._start_capture()
        if old is not None:
            threading.Thread(target=old.stop, name="escort-capture-retire", daemon=True).start()
        self.store.warn("Transcription restarted by you.")
        self._notify()
        return self.public_status()

    def _start_capture(self) -> None:
        """Start screen + audio capture over the store. Never raises; errors land in capture_error."""
        settings = self._settings()
        if not self.store.title:
            self.store.title = "Live meeting"
        try:
            self.live = live_capture.start_session(
                self.store,
                settings,
                self.context.combined_markdown(),
                mic_id=self.prefs.get("mic_id"),
                loopback_id=self.prefs.get("loopback_id"),
                on_pipeline=lambda: self.context.apply_to_store(self.store),
                monitor_source=self._watch_target,
            )
        except Exception as exc:
            log.warning("Live capture failed to start: %s", exc)
            self.store.warn(f"Live capture did not start: {exc}")
            self.live = None

    def _stop_capture(self, timeout: float = 6.0) -> None:
        live = self.live
        if live is not None:
            try:
                live.stop(timeout=timeout)
            except Exception as exc:
                log.warning("Live capture did not stop cleanly: %s", exc)

    # --- proactive nudges ----------------------------------------------------------

    def _start_proactive(self) -> None:
        """Never raises: a failed engine only means no nudges."""
        self._proactive_ctx = None
        try:
            self.proactive = ProactiveEngine(
                self.store,
                self._settings(),
                self._proactive_context,
                gate_fn=lambda: getattr(getattr(self.live, "pipeline", None), "gate", None),
                on_nudge=self._on_nudge,
            ).start()
        except Exception as exc:
            log.warning("Proactive engine did not start: %s", exc)
            self.proactive = None

    def _stop_proactive(self) -> None:
        engine, self.proactive = self.proactive, None
        if engine is not None:
            try:
                engine.stop()
            except Exception as exc:
                log.warning("Proactive engine did not stop cleanly: %s", exc)

    def _proactive_context(self) -> dict[str, Any]:
        """Project, other projects, the slot happening now (with its join link) and the notes. Cached."""
        cached = self._proactive_ctx
        if cached and time.monotonic() - cached[0] < PROACTIVE_CONTEXT_S:
            ctx = dict(cached[1])
        else:
            ctx = {"event": self._current_event(), "notes": self.context.combined_markdown()}
            self._proactive_ctx = (time.monotonic(), ctx)
        active = self.projects.get_active_project()

        def brief(project: Any) -> dict[str, str]:
            return {"id": project.id, "name": project.name, "description": (project.description or "")[:300]}

        ctx["project"] = brief(active) if active else None
        ctx["other_projects"] = [brief(p) for p in self.projects.list_projects() if not active or p.id != active.id]
        return ctx

    def _current_event(self) -> dict[str, Any] | None:
        """The calendar slot under way (or starting within 10 min): the active project's first, then any."""
        from datetime import datetime, timedelta, timezone

        from escort.proactive import _parse_time

        try:
            events = self._synced_calendar_events(25)
        except Exception:
            return None
        now = datetime.now(timezone.utc)

        def live(event: Any) -> bool:
            start, end = _parse_time(event.start), _parse_time(event.end)
            return bool(start and end and start - timedelta(minutes=10) <= now <= end)

        scoped = [e for e in self.projects.active_upcoming_meetings(events) if live(e)]
        current = scoped or [e for e in events if live(e)]
        if not current:
            return None
        event = current[0]
        return {
            "title": event.title,
            "start": event.start,
            "end": event.end,
            "meeting_url": getattr(event, "meeting_url", ""),
            "attendees": list(event.attendees)[:12],
        }

    def _on_nudge(self, nudge: Any) -> None:
        """A normal or urgent nudge opens the overlay, even when it was collapsed to the pill."""
        if nudge.priority != "low" and self.listening:
            self.set_overlay(visible=True, collapsed=False)
        else:
            self._notify()

    def nudge_action(self, nudge_id: str, action: str) -> dict[str, Any]:
        """Carry out a nudge button. Values come from the stored nudge, never from the request."""
        nudge = self.store.nudge(nudge_id)
        if nudge is None:
            return {"ok": False, "error": "unknown nudge", "status": self.public_status()}
        spec = next((item for item in nudge.actions if item.get("id") == action), None)
        if action not in {"dismiss", "snooze"} and spec is None:
            return {"ok": False, "error": "that action is not on this nudge", "status": self.public_status()}
        result: dict[str, Any] = {"ok": True, "action": action}
        if action in {"dismiss", "snooze"}:
            self.store.set_nudge_status(nudge_id, "dismissed")
            if self.proactive is not None:
                self.proactive.note_dismissed(nudge)
        elif action == "open_link":
            import webbrowser

            url = str(spec.get("value") or "")
            if url.startswith("https://"):
                webbrowser.open(url)
            self.store.set_nudge_status(nudge_id, "acted")
        elif action == "switch_project":
            self.set_active_project(str(spec.get("value") or ""))
            self._proactive_ctx = None
            self.store.set_nudge_status(nudge_id, "acted")
        elif action == "ask":
            result["ask"] = str(spec.get("value") or "")
            self.store.set_nudge_status(nudge_id, "acted")
        self._notify()
        result["status"] = self.public_status()
        return result

    def shutdown(self) -> None:
        """App exit: stop capture threads (they are daemons, but release devices first)."""
        self.listening = False
        process = self.overlay_process
        if process is not None:
            # First: it is the one window still on screen.
            try:
                process.stop()
            except Exception as exc:
                log.warning("Overlay process did not stop cleanly: %s", exc)
        self._stop_proactive()
        self._stop_capture(timeout=2.0)

    def delete_meeting(self) -> dict[str, Any]:
        with self._lock:
            self.listening = False
            self.listening_started_at = None
            self._summary_gen += 1
            self.summary = empty_summary()
            self._pending_session = None
            self._stop_proactive()
            self._stop_capture(timeout=2.0)
            self.live = None
            self.meeting_id = new_id("mtg")
            self.store.delete_meeting()
            self.context.clear()
        self._set_overlay_visible(False, bring_main=True)
        self._notify()
        return self.public_status()

    # --- summary -------------------------------------------------------------

    def summary_status(self) -> dict[str, Any]:
        return dict(self.summary)

    def regenerate_summary(self) -> dict[str, Any]:
        with self._lock:
            if not self.listening:
                self._begin_summary(stop_session=None)
        return self.summary_status()

    def transcript(self) -> dict[str, Any]:
        with self.store.lock:
            return {
                "turns": [item.to_dict() for item in self.store.utterances],
                "partials": dict(self.store.partials),
                "listening": self.listening,
                "listening_started_at": self.listening_started_at,
            }

    def _begin_summary(self, stop_session: live_capture.LiveSession | None) -> None:
        self._summary_gen += 1
        generation = self._summary_gen
        self.summary = {**empty_summary("generating"), "title": self.store.title}
        thread = threading.Thread(
            target=self._run_summary, args=(generation, stop_session), name="escort-summary", daemon=True
        )
        self._summary_thread = thread
        thread.start()

    def _run_summary(self, generation: int, stop_session: live_capture.LiveSession | None) -> None:
        if stop_session is not None:
            # Join the capture threads first so the last finished turns are in the snapshot.
            try:
                stop_session.stop()
            except Exception as exc:
                log.warning("Live capture did not stop cleanly: %s", exc)
        snapshot: dict[str, Any] = {}
        try:
            snapshot = self.store.snapshot()
            result = generate_summary(self._settings(), snapshot, self.context.combined_markdown(), self._summary_client)
        except Exception as exc:
            log.warning("Summary failed: %s", exc)
            try:
                result = rules_summary(snapshot, error=str(exc))
            except Exception as inner:
                result = {**empty_summary("error"), "error": str(inner), "generated_at": utc_now()}
        with self._lock:
            if generation != self._summary_gen:
                return
            self.summary = result
            pending = self._pending_session
            if pending and pending.get("project_id") and result.get("status") in {"ready", "error"}:
                try:
                    sessions = self.projects.session_store(str(pending["project_id"]))
                    session_id = new_id("sess")
                    if snapshot.get("snapshots"):
                        snapshot = {**snapshot, "snapshots": sessions.keep_visuals(session_id, snapshot["snapshots"], self.store.media_dir)}
                    sessions.save(
                        session_id=session_id,
                        project_id=str(pending["project_id"]),
                        title=str(pending.get("title") or self.store.title or "Session"),
                        started_at=str(pending.get("started_at") or utc_now()),
                        ended_at=str(pending.get("ended_at") or utc_now()),
                        summary=result,
                        snapshot=snapshot,
                    )
                except Exception as exc:
                    log.warning("Could not persist session recap: %s", exc)
                self._pending_session = None
        self._notify()

    # --- overlay -------------------------------------------------------------

    @property
    def hide_from_share(self) -> bool:
        return bool(self.prefs.get("hide_from_share", True))

    def overlay_supported(self) -> bool:
        if self.overlay is not None:
            return bool(self.overlay.supported)
        from escort.desktop.win_overlay import capture_exclusion_supported

        return capture_exclusion_supported()

    @property
    def overlay_opacity(self) -> float:
        return clamp_opacity(self.prefs.get("overlay_opacity", DEFAULT_OPACITY))

    def overlay_status(self) -> dict[str, Any]:
        return {
            "hide_from_share": self.hide_from_share,
            "visible": self.overlay_visible,
            "listening": self.listening,
            "supported": self.overlay_supported(),
            "collapsed": self.overlay_collapsed,
            "opacity": self.overlay_opacity,
            "input_focus": self.overlay_input_focus,
        }

    def attach_overlay(self, controller: OverlayController | None) -> None:
        """Bind the overlay window and push the current state to it."""
        self.overlay = controller
        if controller is None:
            return
        self._call_overlay("set_hide_from_share", self.hide_from_share)
        self._call_optional("set_opacity", self.overlay_opacity)
        self._call_optional("set_collapsed", self.overlay_collapsed)
        self._call_overlay("set_visible", self.overlay_visible)

    def attach_overlay_process(self, process: OverlayProcess | None) -> None:
        """Electron mode: the overlay polls /api/overlay, so set_overlay only stores flags."""
        self.overlay_process = process

    def overlay_process_lost(self) -> None:
        """The overlay process is gone for good: never leave the user without the main window."""
        if self._main_hidden:
            self._main("show")

    def _overlay_process_running(self) -> bool:
        process = self.overlay_process
        if process is None:
            return False
        try:
            return bool(process.is_running())
        except Exception:
            return False

    def attach_main_window(self, controller: MainWindowController | None) -> None:
        self.main_window = controller

    def main_window_minimized(self) -> None:
        """Still live and the overlay is hidden (the user opened the main window from it): show it again."""
        if self.listening and not self.overlay_visible:
            self.set_overlay(visible=True)

    def show_main_window(self) -> None:
        """Tray 'Show Escort': always restores and raises the main window."""
        self._main("show")

    def set_overlay(
        self,
        hide_from_share: bool | None = None,
        visible: bool | None = None,
        collapsed: bool | None = None,
        opacity: float | None = None,
        input_focus: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if hide_from_share is not None and hide_from_share != self.hide_from_share:
                self.prefs["hide_from_share"] = hide_from_share
                self._write_prefs()
            if hide_from_share is not None:
                self._call_overlay("set_hide_from_share", hide_from_share)
            if opacity is not None:
                value = clamp_opacity(opacity)
                if value != self.prefs.get("overlay_opacity"):
                    self.prefs["overlay_opacity"] = value
                    self._write_prefs()
                self._call_optional("set_opacity", value)
            if collapsed is not None:
                self.overlay_collapsed = collapsed
                self._call_optional("set_collapsed", collapsed)
            if visible is not None:
                self._set_overlay_visible(visible, bring_main=not visible)
            if input_focus is not None and (self.overlay_visible or not input_focus):
                self.overlay_input_focus = input_focus
                self._call_optional("set_input_focus", input_focus)
        self._notify()
        return self.overlay_status()

    def toggle_overlay(self) -> dict[str, Any]:
        return self.set_overlay(visible=not self.overlay_visible)

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Called after listening or overlay state changes (used by the tray)."""
        self._observers.append(callback)

    def _set_overlay_visible(self, visible: bool, bring_main: bool = False) -> None:
        """Show/hide the overlay; the main window hides while the overlay is up.

        The main window hides *before* the overlay shows, so Windows hands
        activation back to the user's previous app instead of the overlay. If
        there is no overlay it is never hidden; if the overlay call fails it is
        shown again at once, so the user is never left without a window. An
        overlay process (Electron) shows itself from the stored flag; the main
        window only hides while that process is actually running.
        """
        self.overlay_visible = visible
        if not visible:
            self.overlay_input_focus = False
            self._call_overlay("set_visible", False)
            if self._main_hidden or bring_main:
                self._main("show")
            return
        if self.overlay is not None:
            hide_main = not self._main_hidden
            if hide_main:
                self._main("hide")
            if not self._call_overlay("set_visible", True) and hide_main:
                self._main("show")
        elif self._overlay_process_running() and not self._main_hidden:
            self._main("hide")

    def _main(self, action: str) -> None:
        if self.main_window is None:
            return
        try:
            getattr(self.main_window, action)()
            self._main_hidden = action == "hide"
        except Exception as exc:
            log.warning("Main window %s failed: %s", action, exc)

    def _call_overlay(self, method: str, value: Any) -> bool:
        if self.overlay is None:
            return False
        try:
            getattr(self.overlay, method)(value)
            return True
        except Exception as exc:
            log.warning("Overlay %s(%s) failed: %s", method, value, exc)
            return False

    def _call_optional(self, method: str, value: Any) -> bool:
        """Like _call_overlay, for setters older controllers (and test fakes) may not have."""
        if self.overlay is None or not hasattr(self.overlay, method):
            return False
        return self._call_overlay(method, value)

    def _notify(self) -> None:
        for callback in list(self._observers):
            try:
                callback()
            except Exception as exc:
                log.warning("State observer failed: %s", exc)

    def act(self, card_id: str, action: str) -> dict[str, Any]:
        card = next((item for item in self.store.cards if item.id == card_id), None)
        if card is None:
            return self.public_status()
        if action == "ignore":
            self.store.warn(f"Ignored {card.title}")
        elif action == "raise":
            from escort.models import CardRecord
            from escort.store import new_id, utc_now

            self.store.add_card(
                CardRecord(
                    id=new_id("card"),
                    type="COMMITMENT",
                    title="Raise it",
                    body=card.body,
                    prd_section=card.prd_section,
                    who="Me",
                    what=card.title,
                    utterance_id=card.utterance_id,
                    trigger_text=card.title,
                    created_at=utc_now(),
                    latency_ms=0,
                    crop_path=None,
                    crop_url=None,
                    source="desktop",
                    deictic=None,
                    segmenter=None,
                    when="this meeting",
                )
            )
        elif action == "suggest":
            self.store.warn(f"Suggest reply for {card.title}")
        return self.public_status()


def demo_runtime(folder: Path) -> DesktopRuntime:
    store = MeetingStore(folder / "media")
    runtime = DesktopRuntime(store, MemorySecretStore(), folder / "config.json")
    return runtime
