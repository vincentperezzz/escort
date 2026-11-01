from __future__ import annotations

from typing import Protocol

from escort.context.models import CalendarEvent, ContextItem


class ContextSource(Protocol):
    name: str
    label: str

    def connected(self) -> bool: ...

    def search(self, query: str, limit: int = 8) -> list[dict]: ...

    def fetch(self, external_id: str) -> ContextItem: ...

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]: ...
