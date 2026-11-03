"""Google installed-app OAuth: loopback redirect + PKCE + state, tokens in the secret store.

Google treats the client secret of a "Desktop app" OAuth client as non-confidential, so it
may ship with the app; it is still read from env/settings rather than hardcoded.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets as stdlib_secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from escort.context.sources.base import (
    ConnectorApiDisabledError,
    ConnectorAuthError,
    ConnectorError,
    ConnectorScopeError,
    ConnectorSetupError,
)
from escort.paths import repo_root

log = logging.getLogger("escort.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# The signed-in account's email: the primary calendar's id. Needs only calendar.readonly.
ACCOUNT_URL = "https://www.googleapis.com/calendar/v3/calendars/primary"
_SCOPE_BASE = "https://www.googleapis.com/auth/"
DOCS_READ_SCOPE = _SCOPE_BASE + "documents.readonly"
SHEETS_READ_SCOPE = _SCOPE_BASE + "spreadsheets.readonly"
SLIDES_READ_SCOPE = _SCOPE_BASE + "presentations.readonly"
DRIVE_FILE_SCOPE = _SCOPE_BASE + "drive.file"
DRIVE_READONLY_SCOPE = _SCOPE_BASE + "drive.readonly"
DOCS_WRITE_SCOPE = _SCOPE_BASE + "documents"
SCOPES = (
    _SCOPE_BASE + "calendar.readonly",
    DOCS_READ_SCOPE,
    DRIVE_READONLY_SCOPE,
)
# Optional "Save notes to Drive": requested incrementally, only when the user turns it on.
# drive.file only reaches files Escort created (or the user picked); never the full "drive" scope.
WRITE_SCOPES = (DRIVE_FILE_SCOPE, DOCS_WRITE_SCOPE)
# Every other capability is asked for on its own, incrementally, the first time it is needed.
SHEETS_SCOPES = (SHEETS_READ_SCOPE,)
SLIDES_SCOPES = (SLIDES_READ_SCOPE,)
PICKER_SCOPES = (DRIVE_FILE_SCOPE,)
DRIVE_BROWSE_SCOPES = (DRIVE_READONLY_SCOPE,)
CAPABILITIES: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "docs": (SCOPES, frozenset({DOCS_READ_SCOPE, DOCS_WRITE_SCOPE})),
    "sheets": (SHEETS_SCOPES, frozenset({SHEETS_READ_SCOPE, _SCOPE_BASE + "spreadsheets"})),
    "slides": (SLIDES_SCOPES, frozenset({SLIDES_READ_SCOPE, _SCOPE_BASE + "presentations"})),
    "drive_browse": (DRIVE_BROWSE_SCOPES, frozenset({DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE})),
    "drive_files": (DRIVE_BROWSE_SCOPES, frozenset({DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE})),
}
CAPABILITY_LABELS = {
    "docs": ("Allow Docs access", "read Google Docs", "needs_docs_consent"),
    "sheets": ("Allow Sheets access", "read Google Sheets", "needs_sheets_consent"),
    "slides": ("Allow Slides access", "read Google Slides", "needs_slides_consent"),
    "drive_browse": ("Allow Drive access", "browse and import from Google Drive", "needs_drive_consent"),
    "drive_files": ("Allow Drive access", "browse and import from Google Drive", "needs_drive_consent"),
}
REVOKE_HELP_URL = "https://myaccount.google.com/permissions"
API_LIBRARY_URL = "https://console.cloud.google.com/apis/library/{service}"
_API_NAMES = {
    "docs.googleapis.com": "Google Docs API",
    "drive.googleapis.com": "Google Drive API",
    "calendar-json.googleapis.com": "Google Calendar API",
    "sheets.googleapis.com": "Google Sheets API",
    "slides.googleapis.com": "Google Slides API",
    "picker.googleapis.com": "Google Picker API",
}
# API host -> capability whose scope a 403 "insufficient scopes" on a read means is missing.
_READ_HOSTS = {
    "docs.googleapis.com": "docs",
    "sheets.googleapis.com": "sheets",
    "slides.googleapis.com": "slides",
    "drive.googleapis.com": "drive_browse",
}


def consent_error(capability: str) -> ConnectorScopeError:
    """The 'Allow X access' reconnect prompt for one capability."""
    button, verb, code = CAPABILITY_LABELS[capability]
    err = ConnectorScopeError(
        PROVIDER,
        f"Escort needs your permission to {verb}. Click {button}, then try again.",
        code=code,
    )
    err.capability = capability
    return err
_SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientScopes"}
_DISABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED"}
TOKEN_KEY = "google_oauth_token"
BUNDLED_FILE = "google_client.json"


def user_client_path() -> Path:
    """The per-user google_client.json: %APPDATA%\\Escort on Windows, ~/.config/Escort elsewhere."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Escort" / BUNDLED_FILE


def bundled_client_path() -> Path:
    """google_client.json: next to Escort.exe when installed, in the repo root in dev."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else repo_root()
    return base / BUNDLED_FILE

CLIENT_SECRET_KEY = "google_client_secret"
API_KEY_KEY = "google_api_key"
PROVIDER = "google"
FLOW_TIMEOUT_S = 300.0
REFRESH_SKEW_S = 60.0

CLOSE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Escort</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#222}}</style>
</head><body><h1>{title}</h1><p>{body}</p><script>try{{window.close();}}catch(e){{}}</script></body></html>"""


def pkce_pair() -> tuple[str, str]:
    verifier = stdlib_secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _page(title: str, body: str) -> str:
    return CLOSE_PAGE.format(title=title, body=body)


@dataclass
class PendingFlow:
    state: str
    verifier: str
    redirect_uri: str
    auth_url: str
    scopes: tuple[str, ...] = SCOPES
    # "signin" for the base sign-in, else the capability being added: notes, sheets, slides, drive_files.
    capability: str = "signin"
    server: ThreadingHTTPServer | None = None
    finished: threading.Event = field(default_factory=threading.Event)


class GoogleAuth:
    def __init__(
        self,
        secrets: Any,
        http: httpx.Client,
        client_id_getter: Callable[[], str | None] | None = None,
        open_browser: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
        bundled_path: Callable[[], Path] | None = None,
        user_path: Callable[[], Path] | None = None,
    ) -> None:
        self._bundled_path = bundled_path or (lambda: bundled_client_path())
        self._user_path = user_path or (lambda: user_client_path())
        self.secrets = secrets
        self.http = http
        self._client_id_getter = client_id_getter or (lambda: None)
        self._open_browser = open_browser if open_browser is not None else webbrowser.open
        self._clock = clock
        self._lock = threading.RLock()
        self._flow: PendingFlow | None = None
        self.last_error: str | None = None
        self.on_change: Callable[[], None] = lambda: None
        self.on_connected: Callable[[], None] = lambda: None

    # ---- configuration -------------------------------------------------
    @staticmethod
    def _read_client_file(path: Path) -> tuple[str, str, str]:
        """(client_id, client_secret, api_key) from a google_client.json file; never logged."""
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return "", "", ""
        if not isinstance(data, dict):
            return "", "", ""
        return tuple(str(data.get(key) or "").strip() for key in ("client_id", "client_secret", "api_key"))  # type: ignore[return-value]

    def _bundled(self) -> tuple[str, str, str]:
        return self._read_client_file(self._bundled_path())

    def _user_file(self) -> tuple[str, str, str]:
        return self._read_client_file(self._user_path())

    def credential_source(self) -> str | None:
        if (os.environ.get("ESCORT_GOOGLE_CLIENT_ID") or "").strip():
            return "env"
        if (self._client_id_getter() or "").strip():
            return "setup"
        if self._user_file()[0]:
            return "user_file"
        if self._bundled()[0]:
            return "bundled"
        return None

    def client_id(self) -> str | None:
        env_id = (os.environ.get("ESCORT_GOOGLE_CLIENT_ID") or "").strip()
        if env_id:
            return env_id
        setup_id = (self._client_id_getter() or "").strip()
        if setup_id:
            return setup_id
        user_id = self._user_file()[0]
        if user_id:
            return user_id
        bundled_id = self._bundled()[0]
        return bundled_id or None

    def client_secret(self) -> str | None:
        # The secret always comes from the same source as the id, so two apps never get mixed.
        source = self.credential_source()
        if source == "bundled":
            value = self._bundled()[1]
        elif source == "user_file":
            value = self._user_file()[1]
        elif source in ("env", "setup"):
            value = os.environ.get("ESCORT_GOOGLE_CLIENT_SECRET") or self.secrets.get(CLIENT_SECRET_KEY) or ""
            if not value and source == "setup":
                user_id, user_secret, _ = self._user_file()
                if user_id and user_id == (self._client_id_getter() or "").strip():
                    value = user_secret
        else:
            value = ""
        return value.strip() or None

    def api_key(self) -> str | None:
        """The browser API key for the Drive file chooser (Picker). Same source as the client id,
        so the key and the App ID always belong to one Cloud project. Never logged."""
        source = self.credential_source()
        if source == "bundled":
            value = self._bundled()[2]
        elif source == "user_file":
            value = self._user_file()[2]
        elif source in ("env", "setup"):
            value = os.environ.get("ESCORT_GOOGLE_API_KEY") or self.secrets.get(API_KEY_KEY) or ""
            if not value and source == "setup":
                user_id, _, user_api_key = self._user_file()
                if user_id and user_id == (self._client_id_getter() or "").strip():
                    value = user_api_key
        else:
            value = ""
        return value.strip() or None

    def app_id(self) -> str | None:
        """The Picker App ID: the Cloud project number, i.e. the client id up to its first '-'."""
        head = (self.client_id() or "").split("-", 1)[0]
        return head if head.isdigit() else None

    def configured(self) -> bool:
        return bool(self.client_id())

    # ---- token storage -------------------------------------------------
    def _load(self) -> dict[str, Any] | None:
        raw = self.secrets.get(TOKEN_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = None
        if not isinstance(data, dict):
            # Legacy: a bare access token pasted via /api/connectors/token.
            return {"access_token": str(raw)}
        if not data.get("access_token") and not data.get("refresh_token"):
            return None
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.secrets.set(TOKEN_KEY, json.dumps(data, separators=(",", ":")))

    def _clear(self, reason: str | None = None) -> None:
        self.secrets.delete(TOKEN_KEY)
        if reason:
            self.last_error = reason
        self.on_change()

    def connected(self) -> bool:
        return self._load() is not None

    def account(self) -> str | None:
        data = self._load()
        return (data or {}).get("account") or None

    def pending(self) -> bool:
        flow = self._flow
        return flow is not None and not flow.finished.is_set()

    def pending_scopes(self) -> tuple[str, ...] | None:
        flow = self._flow
        return flow.scopes if flow is not None and not flow.finished.is_set() else None

    def granted_scopes(self) -> set[str]:
        """Scopes the stored token carries. A legacy pasted token is assumed read-only."""
        data = self._load()
        if data is None:
            return set()
        listed = data.get("scopes")
        if isinstance(listed, list) and listed:
            return {str(item) for item in listed}
        text = data.get("scope")
        return set(str(text).split()) if text else set(SCOPES)

    def has_scopes(self, scopes: tuple[str, ...] | list[str]) -> bool:
        return set(scopes) <= self.granted_scopes()

    def can(self, capability: str) -> bool:
        """Whether the stored token allows a capability: docs, sheets, slides, drive_files or notes."""
        if capability == "notes":
            return self.can_write_notes()
        _request, satisfied_by = CAPABILITIES[capability]
        return self.connected() and bool(self.granted_scopes() & satisfied_by)

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise consent_error(capability)

    def can_read_docs(self) -> bool:
        """Reading a pasted Google Docs link needs documents.readonly (the write scope `documents` covers it)."""
        return self.can("docs")

    def can_write_notes(self) -> bool:
        return self.connected() and self.has_scopes(WRITE_SCOPES)

    def pending_capability(self) -> str | None:
        flow = self._flow
        return flow.capability if flow is not None and not flow.finished.is_set() else None

    # ---- browser flow --------------------------------------------------
    def build_auth_url(
        self,
        redirect_uri: str,
        state: str,
        challenge: str,
        scopes: tuple[str, ...] = SCOPES,
        login_hint: str | None = None,
    ) -> str:
        params = {
            "client_id": self.client_id() or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            # Incremental authorization: the new token keeps every scope granted before.
            "include_granted_scopes": "true",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def prepare(self, redirect_uri: str, scopes: tuple[str, ...] | None = None) -> PendingFlow:
        """Create a pending flow (state + PKCE) for a given redirect URI. No server, no browser.

        `scopes` defaults to the read-only set; pass WRITE_SCOPES to add note saving on top
        of what the user already granted.
        """
        if not self.configured():
            raise ConnectorSetupError(PROVIDER, "Google client ID is not configured")
        wanted = tuple(scopes or SCOPES)
        state = stdlib_secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        hint = self.account()
        flow = PendingFlow(
            state=state,
            verifier=verifier,
            redirect_uri=redirect_uri,
            auth_url=self.build_auth_url(redirect_uri, state, challenge, wanted, login_hint=hint),
            scopes=wanted,
            capability=_capability_for(wanted),
        )
        with self._lock:
            self._cancel_locked()
            self._flow = flow
            self.last_error = None
        return flow

    def begin(self, open_browser: bool = True, timeout_s: float = FLOW_TIMEOUT_S, scopes: tuple[str, ...] | None = None) -> str:
        """Start a loopback listener on 127.0.0.1:<ephemeral>, open the browser, return the auth URL."""
        if not self.configured():
            raise ConnectorSetupError(PROVIDER, "Google client ID is not configured")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        server.daemon_threads = True
        port = server.server_address[1]
        flow = self.prepare(f"http://127.0.0.1:{port}/", scopes=scopes)
        flow.server = server
        threading.Thread(target=server.serve_forever, name="escort-google-oauth", daemon=True).start()
        timer = threading.Timer(timeout_s, self._expire, args=(flow,))
        timer.daemon = True
        timer.start()
        if open_browser:
            try:
                self._open_browser(flow.auth_url)
            except Exception:
                pass
        return flow.auth_url

    def _expire(self, flow: PendingFlow) -> None:
        with self._lock:
            if self._flow is flow and not flow.finished.is_set():
                self.last_error = "Google sign-in timed out"
                self._finish_locked(flow)

    def cancel(self) -> None:
        with self._lock:
            self._cancel_locked()

    def _cancel_locked(self) -> None:
        if self._flow is not None:
            self._finish_locked(self._flow)
            self._flow = None

    def _finish_locked(self, flow: PendingFlow) -> None:
        flow.finished.set()
        server = flow.server
        flow.server = None
        if server is not None:
            def _stop() -> None:
                server.shutdown()
                server.server_close()

            threading.Thread(target=_stop, daemon=True).start()

    def handle_callback(self, params: dict[str, str]) -> tuple[int, str]:
        """Validate a loopback redirect and exchange the code. Returns (http status, html)."""
        with self._lock:
            flow = self._flow
            if flow is None or flow.finished.is_set():
                return 400, _page("Sign-in not in progress", "Start the Google connection again from Escort.")
            state = params.get("state") or ""
            if not state or not hmac.compare_digest(state.encode(), flow.state.encode()):
                # Do not finish the flow: a forged redirect must not cancel the real one.
                return 400, _page("Sign-in rejected", "The request did not match the sign-in Escort started (state mismatch).")
            if params.get("error"):
                self.last_error = f"Google sign-in failed: {params['error']}"
                self._finish_locked(flow)
                return 400, _page("Google sign-in was not completed", "You can close this tab and try again from Escort.")
            code = params.get("code") or ""
            if not code:
                return 400, _page("Sign-in rejected", "No authorization code was returned.")
            self._finish_locked(flow)
        try:
            self.exchange_code(code, flow.verifier, flow.redirect_uri, requested=flow.scopes)
        except ConnectorError as err:
            self.last_error = str(err)
            return 400, _page("Google sign-in failed", "Escort could not finish connecting. You can close this tab and try again.")
        try:
            self.on_connected()
        except Exception:
            pass
        notes_flow = flow.capability == "notes"
        if notes_flow and not self.has_scopes(flow.scopes):
            self.last_error = "Google did not grant note saving; tick both boxes on Google's consent screen"
            return 200, _page("Note saving was not allowed", "Escort stays read-only. You can close this tab and try again from Escort.")
        if notes_flow:
            return 200, _page("Escort can save notes to Google Docs", "You can close this tab and return to Escort.")
        if flow.capability in ("sheets", "slides", "drive_browse", "drive_files"):
            _button, verb, _code = CAPABILITY_LABELS[flow.capability]
            if not self.can(flow.capability):
                self.last_error = f"Google did not allow Escort to {verb}; tick the box on Google's consent screen"
                return 200, _page("Access was not allowed", f"Escort cannot {verb} yet. You can close this tab and try again from Escort.")
            return 200, _page(f"Escort can now {verb}", "You can close this tab and return to Escort.")
        if not self.can_read_docs():
            self.last_error = "Google did not allow reading Google Docs; tick the Docs box on Google's consent screen"
            return 200, _page("Docs access was not allowed", "Calendar is connected, but importing Google Docs links needs the Docs box ticked. You can close this tab and try again from Escort.")
        return 200, _page("Escort is connected to Google", "You can close this tab and return to Escort.")

    def exchange_code(self, code: str, verifier: str, redirect_uri: str, requested: tuple[str, ...] = SCOPES) -> dict[str, Any]:
        form = {
            "code": code,
            "client_id": self.client_id() or "",
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        secret = self.client_secret()
        if secret:
            form["client_secret"] = secret
        payload = self._token_post(form)
        if not payload.get("access_token"):
            raise ConnectorError(PROVIDER, "Google token response had no access token")
        previous = self._load()
        if previous is not None and not payload.get("scope"):
            # No scope echoed back: with include_granted_scopes it is the old grant plus the new one.
            payload = {**payload, "scope": " ".join(sorted(self.granted_scopes() | set(requested)))}
        elif not payload.get("scope"):
            payload = {**payload, "scope": " ".join(requested)}
        data = self._token_record(payload, previous=previous)
        self._save(data)
        data["account"] = self._fetch_account(data["access_token"])
        self._save(data)
        self.last_error = None
        self.on_change()
        return data

    def _token_record(self, payload: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        previous = previous or {}
        expires_in = float(payload.get("expires_in") or 3600)
        scope = str(payload.get("scope") or previous.get("scope") or " ".join(SCOPES))
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or previous.get("refresh_token"),
            "expires_at": self._clock() + expires_in,
            "scope": scope,
            "scopes": sorted(set(scope.split())),
            "account": previous.get("account"),
        }

    def _token_post(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.http.post(TOKEN_URL, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as err:
            raise ConnectorError(PROVIDER, f"Could not reach Google: {err}") from err
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            error = str(payload.get("error") or response.status_code)
            if error in {"invalid_grant", "unauthorized_client", "invalid_client"} or response.status_code == 401:
                raise ConnectorAuthError(PROVIDER, f"Google rejected the credentials ({error})")
            raise ConnectorError(PROVIDER, f"Google token endpoint error ({error})")
        return payload

    def _fetch_account(self, access_token: str) -> str | None:
        try:
            response = self.http.get(
                ACCOUNT_URL,
                params={"fields": "id"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code != 200:
                return None
            return str(response.json().get("id") or "") or None
        except (httpx.HTTPError, ValueError):
            return None

    # ---- access tokens -------------------------------------------------
    def access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            data = self._load()
            if data is None:
                raise ConnectorAuthError(PROVIDER, "Google is not connected")
            expires_at = data.get("expires_at")
            fresh = expires_at is None or float(expires_at) - REFRESH_SKEW_S > self._clock()
            if fresh and not force_refresh and data.get("access_token"):
                return str(data["access_token"])
            refresh_token = data.get("refresh_token")
            if not refresh_token:
                self._clear("Google session expired; connect again")
                raise ConnectorAuthError(PROVIDER, "Google session expired; connect again")
            form = {
                "client_id": self.client_id() or "",
                "grant_type": "refresh_token",
                "refresh_token": str(refresh_token),
            }
            secret = self.client_secret()
            if secret:
                form["client_secret"] = secret
            try:
                payload = self._token_post(form)
            except ConnectorAuthError:
                self._clear("Google access was revoked or expired; connect again")
                raise
            updated = self._token_record(payload, previous=data)
            self._save(updated)
            return str(updated["access_token"])

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Authorized call to a Google API; refreshes once on 401, disconnects if still rejected."""
        headers = dict(kwargs.pop("headers", None) or {})
        for attempt in range(2):
            # A forced refresh without a refresh token clears the session and raises.
            token = self.access_token(force_refresh=attempt > 0)
            try:
                response = self.http.request(method, url, headers={**headers, "Authorization": f"Bearer {token}"}, **kwargs)
            except httpx.HTTPError as err:
                raise ConnectorError(PROVIDER, f"Could not reach Google: {err}") from err
            if response.status_code != 401:
                break
        else:
            self._clear("Google access was revoked; connect again")
            raise ConnectorAuthError(PROVIDER, "Google access was revoked; connect again")
        if response.status_code >= 400:
            raise api_error(response)
        return response

    def disconnect(self) -> None:
        self.cancel()
        data = self._load()
        token = (data or {}).get("refresh_token") or (data or {}).get("access_token")
        if token:
            try:
                self.http.post(REVOKE_URL, data={"token": str(token)})
            except httpx.HTTPError:
                pass
        self.secrets.delete(TOKEN_KEY)
        self.last_error = None
        self.on_change()


def _capability_for(scopes: tuple[str, ...]) -> str:
    if tuple(scopes) == WRITE_SCOPES:
        return "notes"
    for name, (requested, _satisfied) in CAPABILITIES.items():
        if name != "docs" and tuple(scopes) == requested:
            return name
    return "signin"


def _error_reasons(response: httpx.Response) -> tuple[set[str], str | None]:
    """Google error reasons (errors[].reason, details[].reason, status) and the service name, if any."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return set(), None
    if not isinstance(error, dict):
        return set(), None
    reasons = {str(error.get("status") or "")}
    service = None
    for item in error.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            reasons.add(str(item["reason"]))
    for item in error.get("details") or []:
        if not isinstance(item, dict):
            continue
        if item.get("reason"):
            reasons.add(str(item["reason"]))
        metadata = item.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("service"):
            service = str(metadata["service"])
    message = str(error.get("message") or "")
    if "has not been used in project" in message or "is disabled" in message:
        reasons.add("SERVICE_DISABLED")
    reasons.discard("")
    return reasons, service


def api_error(response: httpx.Response) -> ConnectorError:
    """Map a Google API error response to an actionable ConnectorError."""
    reasons, service = _error_reasons(response)
    if response.status_code == 403 and reasons & _SCOPE_REASONS:
        capability = _READ_HOSTS.get(response.request.url.host)
        if capability and response.request.method == "GET":
            return consent_error(capability)
        return ConnectorScopeError(PROVIDER, "Escort needs your permission to save notes to Google Docs. Click Allow note saving.")
    if response.status_code == 403 and reasons & _DISABLED_REASONS:
        host = service or response.request.url.host
        if host == "www.googleapis.com":
            host = "drive.googleapis.com" if "/drive/" in response.request.url.path else "calendar-json.googleapis.com"
        name = _API_NAMES.get(host, "this Google API")
        return ConnectorApiDisabledError(
            PROVIDER,
            f"Enable the {name} in your Google Cloud project, then try again.",
            help_url=API_LIBRARY_URL.format(service=host),
        )
    code = "not_found" if response.status_code == 404 else "permission_denied" if response.status_code == 403 else None
    return ConnectorError(PROVIDER, f"Google API error {response.status_code}: {_error_message(response)}", code=code)


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("status") or error)
    return str(error or payload)[:200]


def _make_handler(auth: GoogleAuth) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/":
                self.send_response(404)
                self.end_headers()
                return
            params = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            status, html = auth.handle_callback(params)
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler
