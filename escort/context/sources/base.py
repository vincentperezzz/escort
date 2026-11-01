from __future__ import annotations

from typing import Protocol

from escort.context.models import CalendarEvent, ContextItem


class ConnectorError(RuntimeError):
    """A connector call failed (network, upstream error, unsupported item)."""

    status_code = 502

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider


class ConnectorAuthError(ConnectorError):
    """The stored credential is missing, expired beyond refresh, or revoked."""

    status_code = 401


class ConnectorSetupError(ConnectorError):
    """The user must fix configuration or input (missing client ID, bad pasted token)."""

    status_code = 400


class ContextSource(Protocol):
    name: str
    label: str

    def connected(self) -> bool: ...

    def search(self, query: str, limit: int = 8) -> list[dict]: ...

    def fetch(self, external_id: str) -> ContextItem: ...

    def list_upcoming_events(self, limit: int = 5) -> list[CalendarEvent]: ...
