"""Connector registry: live Google/Notion clients with an explicit mock fallback.

ESCORT_CONNECTORS:
  mock -> every provider serves mock data (labeled mock), regardless of credentials.
  live -> never mock; a provider that is not connected returns nothing.
  auto (default) -> per provider: live once connected, mock (labeled) until then.
A connected provider never returns mock data unless ESCORT_CONNECTORS=mock.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from escort.context.mock_data import (
    mock_calendar,
    mock_drive_file,
    mock_drive_hits,
    mock_google_link,
    mock_import_item,
    mock_notion_hits,
)
from escort.context.models import CalendarEvent, ContextItem
from escort.context.sources.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorSetupError,
    ContextSource,
)
from escort.context.sources.google_auth import (
    CAPABILITIES,
    PROVIDER as GOOGLE,
    SCOPES,
    WRITE_SCOPES,
    GoogleAuth,
)
from escort.context.sources.google_drive import fetch_drive_file, parse_drive_link, resolve_drive_link, search_drive_files
from escort.context.sources.google_source import (
    fetch_google_link,
    list_calendar_events,
    parse_google_link,
)

from escort.context.sources.notion_mcp import NotionMcp
from escort.context.sources.notion_source import NotionClient

CONSENT_CAPABILITIES = ("docs", "sheets", "slides", "drive_browse", "notes")

MODES = ("auto", "live", "mock")
CALENDAR_TTL_S = 60.0


class FolderWatchHandle:
    def __init__(self, project_id: str, folder_path: str) -> None:
        self.project_id = project_id
        self.folder_path = folder_path
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


_folder_watches: dict[str, FolderWatchHandle] = {}
_folder_watch_lock = threading.Lock()


def register_folder_watch(project_id: str, folder_path: str) -> None:
    clean = str(folder_path or "").strip()
    with _folder_watch_lock:
        existing = _folder_watches.pop(project_id, None)
        if existing:
            existing.stop()
        if clean:
            _folder_watches[project_id] = FolderWatchHandle(project_id, clean)


def stop_folder_watch(project_id: str) -> bool:
    with _folder_watch_lock:
        handle = _folder_watches.pop(project_id, None)
    if handle is None:
        return False
    handle.stop()
    return True


def folder_watch_active(project_id: str) -> bool:
    with _folder_watch_lock:
        handle = _folder_watches.get(project_id)
    return handle is not None and not handle.stopped


def connector_mode() -> str:
    mode = os.environ.get("ESCORT_CONNECTORS", "auto").strip().lower()
    return mode if mode in MODES else "auto"


def use_mock(connected: bool = False) -> bool:
    mode = connector_mode()
    if mode == "mock":
        return True
    if mode == "live":
        return False
    return not connected


class _ReadOnlySecrets:
    """Adapter for callers that only pass a getter (tests, WhatsApp prep)."""

    def __init__(self, getter: Callable[[str], str | None]) -> None:
        self._get = getter

    def get(self, name: str) -> str | None:
        return self._get(name)

    def set(self, name: str, value: str) -> None:
        return None

    def delete(self, name: str) -> None:
        return None


class Connectors:
    def __init__(
        self,
        secrets: Any,
        client_id_getter: Callable[[], str | None] | None = None,
        http: httpx.Client | None = None,
        open_browser: Callable[[str], Any] | None = None,
        bundled_path: Callable[[], Path] | None = None,
        user_path: Callable[[], Path] | None = None,
    ) -> None:
        self.secrets = secrets
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True)
        from escort.context.sources.notion_mcp import launch_auth_window

        browser_launcher = open_browser if open_browser is not None else launch_auth_window
        self.google = GoogleAuth(
            secrets,
            self.http,
            client_id_getter=client_id_getter,
            open_browser=browser_launcher,
            bundled_path=bundled_path,
            user_path=user_path,
        )
        self.notion = NotionClient(secrets, self.http)
        self.notion_mcp = NotionMcp(secrets, self.http, open_browser=browser_launcher)
        self.generation = 0
        self.google.on_change = self._bump
        self.notion.on_change = self._bump
        self.notion_mcp.on_change = self._bump
        # Set by the runtime: whether the user turned on "Save notes to Drive".
        self.save_notes_getter: Callable[[], bool] = lambda: False
        # Set by the runtime: {"save_notes": bool, "parent": {...} | None, "needs_capabilities": bool}.
        self.notion_notes_getter: Callable[[], dict[str, Any]] = lambda: {}

    @classmethod
    def from_getter(cls, getter: Callable[[str], str | None]) -> Connectors:
        return cls(_ReadOnlySecrets(getter))

    def _bump(self) -> None:
        self.generation += 1

    def provider_mode(self, provider: str) -> str:
        return "mock" if use_mock(self.connected(provider)) else "live"

    def connected(self, provider: str) -> bool:
        if provider == "google":
            return self.google.connected()
        if provider == "notion":
            return self.notion_mcp.connected() or self.notion.connected()
        raise KeyError(provider)

    def notion_auth(self) -> str | None:
        """'oauth' (Notion MCP, preferred), 'token' (pasted integration token, REST) or None."""
        if self.notion_mcp.connected():
            return "oauth"
        if self.notion.connected():
            return "token"
        return None

    def status(self) -> dict[str, Any]:
        return {"providers": [self._google_row(), self._notion_row()]}

    def _google_row(self) -> dict[str, Any]:
        connected = self.google.connected()
        configured = self.google.configured()
        pending_capability = self.google.pending_capability()
        if not configured:
            hint = (
                "Add your Google app credentials below: an OAuth client of type 'Desktop app' with the "
                "Calendar and Docs APIs enabled."
            )
        elif connected and pending_capability == "notes":
            hint = "Finish allowing note saving in your browser."
        elif connected and pending_capability in ("sheets", "slides", "drive_browse", "drive_files"):
            hint = "Finish allowing access in your browser."
        elif connected and not self.google.can_read_docs():
            hint = "Connected for Calendar. Allow Docs access to import Google Docs links."
        elif connected and not self.google.can("drive_browse"):
            hint = "Connected for Calendar. Reconnect to grant Drive access and import files from your Drive."
        elif connected and self.google.has_scopes(WRITE_SCOPES):
            hint = "Connected. Escort reads Calendar, Google Docs links, and Drive files, and writes only to the Google Docs it creates."
        elif connected:
            hint = "Connected. Escort reads Calendar events, Google Docs, Sheets, Slides, and Drive files you import (read-only)."
        elif self.google.pending():
            hint = "Finish signing in with Google in your browser."
        else:
            hint = "Click Connect to sign in with Google in your browser."
        if self.google.last_error and (not connected or self.google.pending_scopes() is None):
            hint = f"{self.google.last_error}. {hint}"
        save_notes = bool(self.save_notes_getter())
        write_notes = connected and self.google.has_scopes(WRITE_SCOPES)
        read_docs = self.google.can_read_docs()
        consent = {}
        for capability in CONSENT_CAPABILITIES:
            if self.google.can(capability):
                consent[capability] = "granted"
            elif pending_capability == capability or (capability == "docs" and pending_capability == "signin" and connected):
                consent[capability] = "pending"
            else:
                consent[capability] = "not_granted"
        return {
            "id": "google",
            "label": "Google",
            "connected": connected,
            "account": self.google.account() if connected else None,
            "mode": self.provider_mode("google"),
            "configured": configured,
            "credentials_source": self.google.credential_source(),
            "has_client_secret": bool(self.google.client_secret()),
            # The Drive file chooser needs a browser API key from the same Cloud project; never returned here.
            "has_api_key": bool(self.google.api_key()),
            "needs_drive_consent": connected and not self.google.can("drive_browse"),
            "setup_hint": hint,
            "capabilities": {
                "read": True,
                "read_docs": read_docs,
                "read_sheets": self.google.can("sheets"),
                "read_slides": self.google.can("slides"),
                "browse_drive": self.google.can("drive_browse"),
                "pick_drive_files": self.google.can("drive_browse"),
                "write_notes": write_notes,
            },
            # Per capability: granted | pending | not_granted. Sheets, Slides and Drive picking are asked
            # for the first time they are used, so not_granted there is normal, not a warning.
            "consent": consent,
            # Signed in before Docs reading existed (or Docs was unticked): reconnect to allow it.
            "needs_docs_consent": connected and not read_docs,
            "docs_consent_pending": self.google.pending() and self.google.pending_scopes() == SCOPES,
            "save_notes": save_notes,
            # The user wants note saving but this token lacks drive.file + documents.
            "needs_consent": connected and save_notes and not write_notes,
            "consent_pending": pending_capability == "notes",
        }

    def _notion_row(self) -> dict[str, Any]:
        auth = self.notion_auth()
        connected = auth is not None
        mcp = self.notion_mcp
        needs_reconnect = auth != "oauth" and mcp.needs_reconnect()
        pending = mcp.pending()
        if auth == "oauth":
            hint = "Connected through Notion. Escort sees only the pages you chose on Notion's consent screen."
        elif auth == "token":
            hint = "Connected with an integration token. Only pages shared with your integration are visible."
        elif pending:
            hint = "Finish connecting in your browser: choose the pages Escort can see, then allow access."
        elif needs_reconnect:
            hint = "Notion ended Escort's access. Reconnect Notion to keep using it."
        else:
            hint = "Click Connect Notion. Notion opens in your browser, where you choose the pages Escort can see."
        error = mcp.last_error or self.notion.last_error
        if error and not connected and not pending:
            hint = f"{error}. {hint}"
        notes = self.notion_notes_getter() or {}
        save_notes = bool(notes.get("save_notes"))
        parent = notes.get("parent") if connected else None
        needs_capabilities = bool(notes.get("needs_capabilities")) and connected
        account = (mcp.account() if auth == "oauth" else self.notion.account() if auth == "token" else None)
        return {
            "id": "notion",
            "label": "Notion",
            "connected": connected,
            # oauth: one-click Notion MCP connection; token: pasted integration token; None: not connected.
            "auth": auth,
            "account": account,
            # Workspace of the ended OAuth connection, so the Reconnect prompt can name it.
            "reconnect_account": mcp.account() if needs_reconnect else None,
            "needs_reconnect": needs_reconnect,
            "pending": pending,
            "mode": self.provider_mode("notion"),
            "configured": True,
            "setup_hint": hint,
            # Notion grants capabilities per integration, not per token: write is "ready" once a
            # parent page is picked and no write has been refused with restricted_resource.
            "capabilities": {"read": True, "write_notes": connected and bool(parent) and not needs_capabilities},
            "save_notes": save_notes,
            "parent": parent,
            "targets": (notes.get("targets") or []) if connected else [],
            "needs_parent": connected and save_notes and not parent,
            "needs_capabilities": needs_capabilities,
        }

    def connect_google(self, open_browser: bool = True, notes: bool = False, capability: str | None = None) -> dict[str, str]:
        """Sign in (Calendar + read Docs), or add one capability on top with incremental consent.

        capability: "notes" (drive.file + documents), "sheets", "slides", "drive_browse" (drive.readonly),
        "drive_files" (alias), or "docs" (the base sign-in again: how an older token gains documents.readonly).
        """
        if notes:
            capability = "notes"
        if capability == "drive_files":
            capability = "drive_browse"
        if capability == "notes":
            scopes: tuple[str, ...] | None = WRITE_SCOPES
        elif capability in (None, "docs", "signin"):
            scopes = None
        elif capability in CAPABILITIES:
            scopes = CAPABILITIES[capability][0]
        else:
            raise ConnectorSetupError(GOOGLE, f"Unknown Google capability: {capability!r}", code="bad_request")
        return {"auth_url": self.google.begin(open_browser=open_browser, scopes=scopes)}

    def picker_config(self) -> dict[str, Any]:
        google = self.google
        out: dict[str, Any] = {
            "mode": self.provider_mode("google"),
            "api_key": None,
            "app_id": google.app_id(),
            "access_token": None,
            "scope_ok": google.can("drive_browse"),
            "ready": False,
            "reason": None,
        }
        if out["mode"] == "mock":
            out["reason"] = "demo"
        elif not google.connected():
            out["reason"] = "not_connected"
        elif not out["scope_ok"]:
            out["reason"] = "needs_consent"
        if out["reason"]:
            return out
        key = google.api_key()
        if key:
            out["api_key"] = key
        out["access_token"] = google.access_token()
        out["ready"] = True
        return out

    def resolve_drive_link(self, link: str) -> dict[str, Any]:
        """A pasted Drive file or folder link -> importable rows (demo mode: fictional rows)."""
        if self.provider_mode("google") == "mock":
            file_id = parse_drive_link(link)
            folder = "/folders/" in link
            hits = mock_drive_hits()[:3] if folder else mock_drive_hits()[:1]
            return {"folder": "Demo folder" if folder else None, "hits": hits, "skipped": 0, "truncated": False, "id": file_id}
        if not self.google.connected():
            raise ConnectorAuthError(GOOGLE, "Connect Google first, then paste a Drive link.", code="not_connected")
        return resolve_drive_link(self.google, link)

    def fetch_drive_file(self, file_id: str, mime_type: str = "", name: str = "") -> ContextItem:
        if self.provider_mode("google") == "mock":
            return mock_drive_file(file_id, name)
        if not self.google.connected():
            raise ConnectorAuthError(GOOGLE, "Connect Google first, then pick a file from Drive.", code="not_connected")
        return fetch_drive_file(self.google, file_id, mime_type, name)

    def connect_notion(self, token: str) -> dict[str, Any]:
        """Advanced fallback: connect with a pasted internal-integration token (REST API)."""
        self.notion.connect(token)
        return self.status()

    def connect_notion_oauth(self, open_browser: bool = True) -> dict[str, str]:
        """One-click: open Notion's consent page in the browser; the loopback listener finishes it."""
        return {"auth_url": self.notion_mcp.begin(open_browser=open_browser)}

    def disconnect(self, provider: str) -> dict[str, Any]:
        if provider == "google":
            self.google.disconnect()
        elif provider == "notion":
            self.notion_mcp.disconnect()
            self.notion.disconnect()
        else:
            raise KeyError(provider)
        return self.status()


def _label_mock(hits: list[dict]) -> list[dict]:
    return [{**hit, "mock": True} for hit in hits]


def _filter_hits(hits: list[dict], query: str, limit: int) -> list[dict]:
    if not query.strip():
        return hits[:limit]
    q = query.lower()
    return [item for item in hits if q in item["title"].lower() or q in item["snippet"].lower()][:limit]


class NotionSource:
    name = "notion"
    label = "Notion"

    def __init__(self, connectors: Connectors) -> None:
        self.connectors = connectors

    def connected(self) -> bool:
        return self.connectors.connected("notion")

    def mode(self) -> str:
        return self.connectors.provider_mode("notion")

    def _client(self) -> Any:
        """Notion MCP when connected through OAuth, else the pasted-token REST client."""
        return self.connectors.notion_mcp if self.connectors.notion_auth() == "oauth" else self.connectors.notion

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if self.mode() == "mock":
            return _label_mock(_filter_hits(mock_notion_hits(), query, limit))
        if not self.connected():
            return []
        return self._client().search(query, limit)

    def fetch(self, external_id: str) -> ContextItem:
        if self.mode() == "mock":
            return mock_import_item("notion", external_id)
        if not self.connected():
            raise ConnectorAuthError("notion", "Notion is not connected")
        return self._client().fetch(external_id)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class GoogleDriveSource:
    name = "google"
    label = "Google Drive"

    def __init__(self, connectors: Connectors) -> None:
        self.connectors = connectors

    def connected(self) -> bool:
        return self.connectors.google.connected()

    def mode(self) -> str:
        return self.connectors.provider_mode("google")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if self.mode() == "mock":
            return _label_mock(_filter_hits(mock_drive_hits(), query, limit))
        if not self.connected():
            return []
        return search_drive_files(self.connectors.google, query, limit)

    def fetch(self, external_id: str) -> ContextItem:
        if self.mode() == "mock":
            return mock_drive_file(external_id, "")
        if not self.connected():
            raise ConnectorAuthError("google", "Google is not connected")
        return fetch_drive_file(self.connectors.google, external_id)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class GoogleDocsSource:
    name = "google_docs"
    label = "Google Docs"

    def __init__(self, connectors: Connectors, default_kind: str = "docs") -> None:
        self.connectors = connectors
        self.default_kind = default_kind
        if default_kind != "docs":
            self.name = f"google_{default_kind}"
            self.label = f"Google {default_kind.title()}"

    def connected(self) -> bool:
        return self.connectors.google.connected()

    def mode(self) -> str:
        return self.connectors.provider_mode("google")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        return []

    def fetch(self, external_id: str) -> ContextItem:
        """`external_id` is whatever the user pasted: a docs.google.com link or a bare id."""
        kind, file_id = parse_google_link(external_id, self.default_kind)  # rejects Drive/Forms links in every mode
        if self.mode() == "mock":
            return mock_google_link(kind, file_id)
        if not self.connected():
            raise ConnectorAuthError("google", "Google is not connected")
        return fetch_google_link(self.connectors.google, external_id, self.default_kind)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class CalendarSource:
    name = "google_calendar"
    label = "Google Calendar"

    def __init__(self, connectors: Connectors, ttl_s: float = CALENDAR_TTL_S) -> None:
        self.connectors = connectors
        self.ttl_s = ttl_s
        self._cache: tuple[float, tuple[str, int], list[CalendarEvent]] | None = None
        self._lock = threading.Lock()
        self.on_events_fetched: Callable[[list[CalendarEvent]], None] | None = None

    def connected(self) -> bool:
        return self.connectors.google.connected()

    def mode(self) -> str:
        return self.connectors.provider_mode("google")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        return []

    def fetch(self, external_id: str) -> ContextItem:
        raise ConnectorError("google", "calendar does not import pages")

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        with self._lock:
            now = time.monotonic()
            cache_key = (self.mode(), self.connectors.generation)
            cached = self._cache
            if cached and cached[1] == cache_key and now - cached[0] < self.ttl_s:
                return cached[2][:limit]
            if self.mode() == "mock":
                events = mock_calendar()
            elif not self.connected():
                events = []
            else:
                try:
                    events = list_calendar_events(self.connectors.google, max(limit, 10))
                except ConnectorError:
                    self._cache = (now, cache_key, [])
                    raise
            self._cache = (now, cache_key, events)
            hook = self.on_events_fetched
            if hook and events:
                hook(list(events))
            return events[:limit]


def build_sources(connectors: Connectors | Callable[[str], str | None]) -> dict[str, ContextSource]:
    if not isinstance(connectors, Connectors):
        connectors = Connectors.from_getter(connectors)
    drive = GoogleDriveSource(connectors)
    return {
        "notion": NotionSource(connectors),
        "google": drive,
        "drive": drive,
        "google_docs": GoogleDocsSource(connectors),
        "google_sheets": GoogleDocsSource(connectors, "sheets"),
        "google_slides": GoogleDocsSource(connectors, "slides"),
        "google_calendar": CalendarSource(connectors),
    }
