from __future__ import annotations

from typing import Protocol

from escort.context.models import CalendarEvent, ContextItem


class ConnectorError(RuntimeError):
    """A connector call failed (network, upstream error, unsupported item)."""

    status_code = 502
    code: str | None = None
    # For a missing-consent error: which capability to ask for (docs, sheets, slides, drive_files).
    capability: str | None = None

    def __init__(self, provider: str, message: str, *, code: str | None = None, help_url: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        if code:
            self.code = code
        self.help_url = help_url

    def payload(self) -> dict[str, str]:
        """JSON body for the HTTP API; never includes credentials."""
        body = {"error": str(self), "provider": self.provider}
        if self.code:
            body["code"] = self.code
        if self.help_url:
            body["help_url"] = self.help_url
        if self.capability:
            body["capability"] = self.capability
        return body


class ConnectorAuthError(ConnectorError):
    """The stored credential is missing, expired beyond refresh, or revoked."""

    status_code = 401


class ConnectorScopeError(ConnectorError):
    """Connected, but the user has not granted the scope this action needs (re-consent fixes it)."""

    status_code = 403
    code = "needs_consent"


class ConnectorApiDisabledError(ConnectorError):
    """The Google API is not enabled in the user's Cloud project."""

    status_code = 409
    code = "api_disabled"


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
