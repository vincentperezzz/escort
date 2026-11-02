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
    ConnectorAuthError,
    ConnectorError,
    ConnectorSetupError,
)
from escort.paths import repo_root

log = logging.getLogger("escort.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)
TOKEN_KEY = "google_oauth_token"
BUNDLED_FILE = "google_client.json"


def bundled_client_path() -> Path:
    """google_client.json: next to Escort.exe when installed, in the repo root in dev."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else repo_root()
    return base / BUNDLED_FILE

CLIENT_SECRET_KEY = "google_client_secret"
PROVIDER = "google"
FLOW_TIMEOUT_S = 300.0
REFRESH_SKEW_S = 60.0

CLOSE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Escort</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#222}}</style>
</head><body><h1>{title}</h1><p>{body}</p></body></html>"""


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
    ) -> None:
        self._bundled_path = bundled_path or (lambda: bundled_client_path())
        self.secrets = secrets
        self.http = http
        self._client_id_getter = client_id_getter or (lambda: None)
        self._open_browser = open_browser if open_browser is not None else webbrowser.open
        self._clock = clock
        self._lock = threading.RLock()
        self._flow: PendingFlow | None = None
        self.last_error: str | None = None
        self.on_change: Callable[[], None] = lambda: None

    # ---- configuration -------------------------------------------------
    def _bundled(self) -> tuple[str, str]:
        """(client_id, client_secret) from the optional installer-supplied file; never logged."""
        try:
            data = json.loads(self._bundled_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", ""
        if not isinstance(data, dict):
            return "", ""
        return str(data.get("client_id") or "").strip(), str(data.get("client_secret") or "").strip()

    def credential_source(self) -> str | None:
        """Where the client credentials come from: env > saved Setup > bundled file."""
        if (os.environ.get("ESCORT_GOOGLE_CLIENT_ID") or "").strip():
            return "env"
        if (self._client_id_getter() or "").strip():
            return "setup"
        if self._bundled()[0]:
            return "bundled"
        return None

    def client_id(self) -> str | None:
        source = self.credential_source()
        if source == "env":
            value = os.environ.get("ESCORT_GOOGLE_CLIENT_ID", "")
        elif source == "setup":
            value = self._client_id_getter() or ""
        elif source == "bundled":
            value = self._bundled()[0]
        else:
            value = ""
        return value.strip() or None

    def client_secret(self) -> str | None:
        # The secret always comes from the same source as the id, so two apps never get mixed.
        source = self.credential_source()
        if source == "bundled":
            value = self._bundled()[1]
        elif source in ("env", "setup"):
            value = os.environ.get("ESCORT_GOOGLE_CLIENT_SECRET") or self.secrets.get(CLIENT_SECRET_KEY) or ""
        else:
            value = ""
        return value.strip() or None

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

    # ---- browser flow --------------------------------------------------
    def build_auth_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        params = {
            "client_id": self.client_id() or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def prepare(self, redirect_uri: str) -> PendingFlow:
        """Create a pending flow (state + PKCE) for a given redirect URI. No server, no browser."""
        if not self.configured():
            raise ConnectorSetupError(PROVIDER, "Google client ID is not configured")
        state = stdlib_secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        flow = PendingFlow(state=state, verifier=verifier, redirect_uri=redirect_uri, auth_url=self.build_auth_url(redirect_uri, state, challenge))
        with self._lock:
            self._cancel_locked()
            self._flow = flow
            self.last_error = None
        return flow

    def begin(self, open_browser: bool = True, timeout_s: float = FLOW_TIMEOUT_S) -> str:
        """Start a loopback listener on 127.0.0.1:<ephemeral>, open the browser, return the auth URL."""
        if not self.configured():
            raise ConnectorSetupError(PROVIDER, "Google client ID is not configured")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        server.daemon_threads = True
        port = server.server_address[1]
        flow = self.prepare(f"http://127.0.0.1:{port}/")
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
            self.exchange_code(code, flow.verifier, flow.redirect_uri)
        except ConnectorError as err:
            self.last_error = str(err)
            return 400, _page("Google sign-in failed", "Escort could not finish connecting. You can close this tab and try again.")
        return 200, _page("Escort is connected to Google", "You can close this tab and return to Escort.")

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
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
        data = self._token_record(payload, previous=None)
        self._save(data)
        data["account"] = self._fetch_account(data["access_token"])
        self._save(data)
        self.last_error = None
        self.on_change()
        return data

    def _token_record(self, payload: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        previous = previous or {}
        expires_in = float(payload.get("expires_in") or 3600)
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or previous.get("refresh_token"),
            "expires_at": self._clock() + expires_in,
            "scope": payload.get("scope") or previous.get("scope") or " ".join(SCOPES),
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
                ABOUT_URL,
                params={"fields": "user(emailAddress,displayName)"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code != 200:
                return None
            user = response.json().get("user") or {}
            return user.get("emailAddress") or user.get("displayName") or None
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
            raise ConnectorError(PROVIDER, f"Google API error {response.status_code}: {_error_message(response)}")
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
