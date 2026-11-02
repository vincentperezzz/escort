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
    mock_drive_hits,
    mock_import_item,
    mock_notion_hits,
)
from escort.context.models import CalendarEvent, ContextItem
from escort.context.sources.base import (
    ConnectorAuthError,
    ConnectorError,
    ContextSource,
)
from escort.context.sources.google_auth import GoogleAuth
from escort.context.sources.google_source import (
    fetch_drive_item,
    list_calendar_events,
    search_drive,
)
from escort.context.sources.notion_source import NotionClient

MODES = ("auto", "live", "mock")
CALENDAR_TTL_S = 60.0


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
    ) -> None:
        self.secrets = secrets
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True)
        self.google = GoogleAuth(secrets, self.http, client_id_getter=client_id_getter, open_browser=open_browser, bundled_path=bundled_path)
        self.notion = NotionClient(secrets, self.http)
        self.generation = 0
        self.google.on_change = self._bump
        self.notion.on_change = self._bump

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
            return self.notion.connected()
        raise KeyError(provider)

    def status(self) -> dict[str, Any]:
        return {"providers": [self._google_row(), self._notion_row()]}

    def _google_row(self) -> dict[str, Any]:
        connected = self.google.connected()
        configured = self.google.configured()
        if not configured:
            hint = (
                "Add your Google app credentials below: an OAuth client of type 'Desktop app' with the "
                "Calendar and Drive APIs enabled."
            )
        elif connected:
            hint = "Connected. Escort reads upcoming Calendar events and Drive files (read-only)."
        elif self.google.pending():
            hint = "Finish signing in with Google in your browser."
        else:
            hint = "Click Connect to sign in with Google in your browser."
        if self.google.last_error and not connected:
            hint = f"{self.google.last_error}. {hint}"
        return {
            "id": "google",
            "label": "Google Calendar & Drive",
            "connected": connected,
            "account": self.google.account() if connected else None,
            "mode": self.provider_mode("google"),
            "configured": configured,
            "credentials_source": self.google.credential_source(),
            "has_client_secret": bool(self.google.client_secret()),
            "setup_hint": hint,
        }

    def _notion_row(self) -> dict[str, Any]:
        connected = self.notion.connected()
        if connected:
            hint = "Connected. Only pages shared with your integration are visible."
        else:
            hint = (
                "Create an internal integration at notion.so/my-integrations, share the pages you want "
                "with it (page menu > Connections), then paste its secret here."
            )
            if self.notion.last_error:
                hint = f"{self.notion.last_error}. {hint}"
        return {
            "id": "notion",
            "label": "Notion",
            "connected": connected,
            "account": self.notion.account() if connected else None,
            "mode": self.provider_mode("notion"),
            "configured": True,
            "setup_hint": hint,
        }

    def connect_google(self, open_browser: bool = True) -> dict[str, str]:
        return {"auth_url": self.google.begin(open_browser=open_browser)}

    def connect_notion(self, token: str) -> dict[str, Any]:
        self.notion.connect(token)
        return self.status()

    def disconnect(self, provider: str) -> dict[str, Any]:
        if provider == "google":
            self.google.disconnect()
        elif provider == "notion":
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
        return self.connectors.notion.connected()

    def mode(self) -> str:
        return self.connectors.provider_mode("notion")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if self.mode() == "mock":
            return _label_mock(_filter_hits(mock_notion_hits(), query, limit))
        if not self.connected():
            return []
        return self.connectors.notion.search(query, limit)

    def fetch(self, external_id: str) -> ContextItem:
        if self.mode() == "mock":
            return mock_import_item("notion", external_id)
        if not self.connected():
            raise ConnectorAuthError("notion", "Notion is not connected")
        return self.connectors.notion.fetch(external_id)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class DriveSource:
    name = "drive"
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
        return search_drive(self.connectors.google, query, limit)

    def fetch(self, external_id: str) -> ContextItem:
        if self.mode() == "mock":
            return mock_import_item("drive", external_id)
        if not self.connected():
            raise ConnectorAuthError("google", "Google is not connected")
        return fetch_drive_item(self.connectors.google, external_id)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class CalendarSource:
    name = "google_calendar"
    label = "Google Calendar"

    def __init__(self, connectors: Connectors, ttl_s: float = CALENDAR_TTL_S) -> None:
        self.connectors = connectors
        self.ttl_s = ttl_s
        self._cache: tuple[float, int, list[CalendarEvent]] | None = None
        self._lock = threading.Lock()

    def connected(self) -> bool:
        return self.connectors.google.connected()

    def mode(self) -> str:
        return self.connectors.provider_mode("google")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        return []

    def fetch(self, external_id: str) -> ContextItem:
        raise ConnectorError("google", "calendar does not import pages")

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        if self.mode() == "mock":
            return mock_calendar()[:limit]
        if not self.connected():
            return []
        with self._lock:
            now = time.monotonic()
            cached = self._cache
            if cached and cached[1] == self.connectors.generation and now - cached[0] < self.ttl_s:
                return cached[2][:limit]
            # Fetch a fixed window so small and large callers share one cache entry.
            try:
                events = list_calendar_events(self.connectors.google, max(limit, 10))
            except ConnectorError:
                # Back off for one TTL instead of hitting Google on every status poll.
                self._cache = (now, self.connectors.generation, [])
                raise
            self._cache = (now, self.connectors.generation, events)
            return events[:limit]


def build_sources(connectors: Connectors | Callable[[str], str | None]) -> dict[str, ContextSource]:
    if not isinstance(connectors, Connectors):
        connectors = Connectors.from_getter(connectors)
    return {
        "notion": NotionSource(connectors),
        "drive": DriveSource(connectors),
        "google_calendar": CalendarSource(connectors),
    }
