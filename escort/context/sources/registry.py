from __future__ import annotations

import os

from escort.context.mock_data import mock_calendar, mock_drive_hits, mock_import_item, mock_notion_hits
from escort.context.models import CalendarEvent, ContextItem
from escort.context.sources.base import ContextSource


def use_mock() -> bool:
    mode = os.environ.get("ESCORT_CONNECTORS", "auto").lower()
    if mode == "mock":
        return True
    if mode == "live":
        return False
    return True


class MockNotionSource:
    name = "notion"
    label = "Notion"

    def connected(self) -> bool:
        return True

    def search(self, query: str, limit: int = 8) -> list[dict]:
        hits = mock_notion_hits()
        if not query.strip():
            return hits[:limit]
        q = query.lower()
        return [item for item in hits if q in item["title"].lower() or q in item["snippet"].lower()][:limit]

    def fetch(self, external_id: str) -> ContextItem:
        return mock_import_item("notion", external_id)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class MockDriveSource:
    name = "drive"
    label = "Google Drive"

    def connected(self) -> bool:
        return True

    def search(self, query: str, limit: int = 8) -> list[dict]:
        hits = mock_drive_hits()
        if not query.strip():
            return hits[:limit]
        q = query.lower()
        return [item for item in hits if q in item["title"].lower() or q in item["snippet"].lower()][:limit]

    def fetch(self, external_id: str) -> ContextItem:
        return mock_import_item("drive", external_id)

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return []


class MockCalendarSource:
    name = "google_calendar"
    label = "Google Calendar"

    def connected(self) -> bool:
        return True

    def search(self, query: str, limit: int = 8) -> list[dict]:
        return []

    def fetch(self, external_id: str) -> ContextItem:
        raise ValueError("calendar does not import pages")

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        return mock_calendar()[:limit]


class LiveNotionSource(MockNotionSource):
    def connected(self) -> bool:
        return bool(self._token())

    def _token(self) -> str | None:
        return None

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if not self.connected():
            return MockNotionSource().search(query, limit)
        return MockNotionSource().search(query, limit)

    def fetch(self, external_id: str) -> ContextItem:
        if not self.connected():
            return MockNotionSource().fetch(external_id)
        return MockNotionSource().fetch(external_id)


class LiveDriveSource(MockDriveSource):
    def connected(self) -> bool:
        return bool(self._token())

    def _token(self) -> str | None:
        return None

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if not self.connected():
            return MockDriveSource().search(query, limit)
        return MockDriveSource().search(query, limit)

    def fetch(self, external_id: str) -> ContextItem:
        if not self.connected():
            return MockDriveSource().fetch(external_id)
        return MockDriveSource().fetch(external_id)


class LiveCalendarSource(MockCalendarSource):
    def connected(self) -> bool:
        return bool(self._token())

    def _token(self) -> str | None:
        return None

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]:
        if not self.connected():
            return MockCalendarSource().list_upcoming_events(limit)
        return MockCalendarSource().list_upcoming_events(limit)


def build_sources(secrets_get) -> dict[str, ContextSource]:
    mock = use_mock()

    def token(name: str) -> str | None:
        if mock:
            return None
        return secrets_get(name)

    notion = LiveNotionSource()
    drive = LiveDriveSource()
    calendar = LiveCalendarSource()
    notion._token = lambda: token("notion_oauth_token")  # type: ignore[method-assign]
    drive._token = lambda: token("google_oauth_token")  # type: ignore[method-assign]
    calendar._token = lambda: token("google_oauth_token")  # type: ignore[method-assign]
    return {
        "notion": notion,
        "drive": drive,
        "google_calendar": calendar,
    }
