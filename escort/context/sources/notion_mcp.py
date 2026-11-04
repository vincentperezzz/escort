"""Notion through its hosted MCP server: one-click OAuth in the user's browser, then MCP tool calls.

Connect: discovery (RFC 9728 protected-resource metadata -> RFC 8414 authorization-server metadata),
dynamic client registration (RFC 7591) done once and reused, then an authorization-code flow with
PKCE S256 and a state check on a custom-scheme redirect (escort://oauth/notion). The user picks
on Notion's own consent page which pages Escort can see. Nothing is hardcoded but the MCP URL.

Tokens live in the secret store (keyring) as one JSON record, so a rotated refresh token is always
saved together with its access token. Refreshes are serialized with a lock and happen shortly before
expiry; invalid_grant is terminal: the tokens are dropped and the status says Reconnect Notion.

The access token only works against Notion MCP, never the REST API. Tool names and argument shapes
come from tools/list at runtime; `NotionMcp` maps Escort's needs (search, fetch as markdown, create a
page, insert/replace lines) onto whatever the server offers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets as stdlib_secrets
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from escort.context.chunk import sections_from_markdown
from escort.context.models import ContextItem
from escort.context.sources.base import ConnectorAuthError, ConnectorError, ConnectorSetupError
from escort.context.sources.google_auth import _page, pkce_pair
from escort.store import new_id

log = logging.getLogger("escort.notion.mcp")

PROVIDER = "notion"
MCP_URL = "https://mcp.notion.com/mcp"
TOKEN_KEY = "notion_mcp_token"
CLIENT_KEY = "notion_mcp_client"
CLIENT_NAME = "Escort"
CLIENT_VERSION = "0.1.1"
PROTOCOL_VERSION = "2025-06-18"
NOTION_OAUTH_REDIRECT_URI = "escort://oauth/notion"
FLOW_TIMEOUT_S = 600.0
REFRESH_SKEW_S = 300.0
TERMINAL_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client"}
REDIRECT_REJECTED = {"invalid_redirect_uri", "invalid_client_metadata"}

# capability -> (preferred tool names, words the name must contain, words it must not contain)
TOOL_HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "search": (("notion-search", "search"), ("search",), ("user", "connector", "ai-", "teamspace")),
    "fetch": (("notion-fetch", "fetch"), ("fetch",), ("async", "file")),
    "create_page": (("notion-create-pages", "create-pages", "notion-create-page"), ("create", "page"), ("database", "comment", "view")),
    "update_page": (("notion-update-page", "update-page"), ("update", "page"), ("database", "data-source", "view")),
    "self": (("notion-get-self", "API-get-self", "get-self"), ("self",), ()),
}
TOOL_LABELS = {
    "search": "search pages",
    "fetch": "read pages",
    "create_page": "create pages",
    "update_page": "update pages",
    "self": "name the workspace",
}


class NotionReconnectError(ConnectorAuthError):
    """The grant is gone (invalid_grant or rejected tokens): the user must connect again."""

    code = "needs_reconnect"


@dataclass
class PendingFlow:
    state: str
    verifier: str
    redirect_uri: str
    auth_url: str
    client: dict[str, Any]
    meta: dict[str, Any]
    timer: threading.Timer | None = None
    verifiers: dict[str, str] = field(default_factory=dict)
    finished: threading.Event = field(default_factory=threading.Event)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def notion_oauth_callback_params(url: str) -> dict[str, str] | None:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != "escort":
        return None
    path = f"{parts.netloc}{parts.path}".strip("/")
    if path != "oauth/notion":
        return None
    return {key: values[0] for key, values in urllib.parse.parse_qs(parts.query).items() if values}


def launch_auth_window(url: str) -> bool:
    """Opens OAuth in an isolated native app window on Windows (Edge app mode) to prevent
    third-party browser shields (such as Brave) from interfering with the OAuth redirect.
    Falls back to webbrowser.open() if Edge is not found or on non-Windows.
    """
    if sys.platform == "win32":
        for env_var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env_var)
            if not base:
                continue
            edge = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if edge.is_file():
                try:
                    subprocess.Popen([str(edge), f"--app={url}", "--window-size=680,780"])
                    return True
                except Exception as exc:
                    log.warning("Could not launch Edge auth window: %s", exc)
                    break
    return bool(webbrowser.open(url))


class NotionMcp:
    def __init__(
        self,
        secrets: Any,
        http: httpx.Client,
        mcp_url: str = MCP_URL,
        open_browser: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.secrets = secrets
        self.http = http
        self.mcp_url = mcp_url
        self._open_browser = open_browser if open_browser is not None else launch_auth_window
        self._clock = clock
        self._flow_lock = threading.RLock()
        self._token_lock = threading.Lock()  # one refresh at a time: refresh tokens rotate
        self._session_lock = threading.RLock()
        self._flow: PendingFlow | None = None
        self._meta: dict[str, Any] | None = None
        self._session_id: str | None = None
        self._protocol: str | None = None
        self._initialized = False
        self._tools: list[dict[str, Any]] | None = None
        self._rpc_id = 0
        self.last_error: str | None = None
        self.on_change: Callable[[], None] = lambda: None
        self.on_connected: Callable[[], None] = lambda: None

    # ---- stored state ---------------------------------------------------------------
    def _load_json(self, key: str) -> dict[str, Any] | None:
        raw = self.secrets.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def _tokens(self) -> dict[str, Any] | None:
        data = self._load_json(TOKEN_KEY)
        if data and (data.get("access_token") or data.get("refresh_token")):
            return data
        return None

    def _save_tokens(self, data: dict[str, Any]) -> None:
        # One write: the rotated refresh token and its access token are never stored apart.
        self.secrets.set(TOKEN_KEY, json.dumps(data, separators=(",", ":")))

    def connected(self) -> bool:
        return self._tokens() is not None

    def needs_reconnect(self) -> bool:
        data = self._load_json(TOKEN_KEY)
        return bool(data and data.get("needs_reconnect") and not self.connected())

    def account(self) -> str | None:
        data = self._load_json(TOKEN_KEY) or {}
        return data.get("account") or None

    def client_registration(self) -> dict[str, Any] | None:
        return self._load_json(CLIENT_KEY)

    def pending(self) -> bool:
        flow = self._flow
        return flow is not None and not flow.finished.is_set()

    def _reset_session(self) -> None:
        with self._session_lock:
            self._session_id = None
            self._protocol = None
            self._initialized = False
            self._tools = None

    def _mark_reconnect(self, reason: str) -> None:
        """Drop the tokens but remember the workspace, so the card can say Reconnect Notion."""
        account = self.account()
        self.secrets.set(TOKEN_KEY, json.dumps({"needs_reconnect": True, "account": account}))
        self.last_error = reason
        self._reset_session()
        log.info("Notion MCP grant ended; reconnect needed")
        self.on_change()

    # ---- discovery --------------------------------------------------------------------
    def _get_json(self, url: str) -> dict[str, Any] | None:
        try:
            response = self.http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as err:
            raise ConnectorError(PROVIDER, f"Could not reach Notion: {err}") from err
        if response.status_code != 200:
            return None
        return _json(response) or None

    def discover(self) -> dict[str, Any]:
        if self._meta is not None:
            return self._meta
        parts = urllib.parse.urlsplit(self.mcp_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        resource_meta = None
        for url in (f"{origin}/.well-known/oauth-protected-resource{parts.path.rstrip('/')}", f"{origin}/.well-known/oauth-protected-resource"):
            data = self._get_json(url)
            if data and data.get("authorization_servers"):
                resource_meta = data
                break
        issuer = str((resource_meta or {}).get("authorization_servers", [origin])[0]).rstrip("/")
        issuer_parts = urllib.parse.urlsplit(issuer)
        issuer_origin = f"{issuer_parts.scheme}://{issuer_parts.netloc}"
        suffix = issuer_parts.path.rstrip("/")
        server_meta = None
        for url in (
            f"{issuer_origin}/.well-known/oauth-authorization-server{suffix}",
            f"{issuer_origin}/.well-known/openid-configuration{suffix}",
            f"{issuer}/.well-known/openid-configuration",
        ):
            data = self._get_json(url)
            if data and data.get("authorization_endpoint") and data.get("token_endpoint"):
                server_meta = data
                break
        if server_meta is None:
            raise ConnectorError(PROVIDER, "Notion did not publish its sign-in details (authorization server metadata)")
        methods = server_meta.get("code_challenge_methods_supported")
        if isinstance(methods, list) and methods and "S256" not in methods:
            raise ConnectorSetupError(PROVIDER, "Notion's sign-in does not support PKCE S256", code="no_pkce")
        self._meta = {
            "resource": str((resource_meta or {}).get("resource") or self.mcp_url),
            "issuer": str(server_meta.get("issuer") or issuer),
            "authorization_endpoint": str(server_meta["authorization_endpoint"]),
            "token_endpoint": str(server_meta["token_endpoint"]),
            "registration_endpoint": server_meta.get("registration_endpoint"),
            "revocation_endpoint": server_meta.get("revocation_endpoint"),
            "auth_methods": list(server_meta.get("token_endpoint_auth_methods_supported") or []),
            "iss_parameter": bool(server_meta.get("authorization_response_iss_parameter_supported")),
        }
        return self._meta

    # ---- dynamic client registration (once, reused) ---------------------------------
    def _registration_has_redirect(self, record: dict[str, Any]) -> bool:
        uris = record.get("redirect_uris") or []
        return NOTION_OAUTH_REDIRECT_URI in [str(uri) for uri in uris]

    def ensure_client(self, meta: dict[str, Any], force_new: bool = False) -> dict[str, Any]:
        existing = self.client_registration()
        if (
            existing
            and existing.get("client_id")
            and existing.get("issuer") == meta["issuer"]
            and self._registration_has_redirect(existing)
            and not force_new
        ):
            return existing
        if existing and not self._registration_has_redirect(existing):
            self.secrets.delete(CLIENT_KEY)
        endpoint = meta.get("registration_endpoint")
        if not endpoint:
            raise ConnectorSetupError(PROVIDER, "Notion does not offer app registration; use an integration token instead.", code="no_registration")
        methods = meta.get("auth_methods") or []
        auth_method = "none" if not methods or "none" in methods else "client_secret_post" if "client_secret_post" in methods else "client_secret_basic"
        uris = [NOTION_OAUTH_REDIRECT_URI]
        body = {
            "client_name": CLIENT_NAME,
            "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": auth_method,
        }
        try:
            response = self.http.post(str(endpoint), json=body, headers={"Accept": "application/json"})
        except httpx.HTTPError as err:
            raise ConnectorError(PROVIDER, f"Could not reach Notion: {err}") from err
        payload = _json(response)
        if response.status_code in (200, 201) and payload.get("client_id"):
            record = {
                "client_id": str(payload["client_id"]),
                "client_secret": payload.get("client_secret"),
                "redirect_uris": [str(uri) for uri in payload.get("redirect_uris") or uris],
                "token_endpoint_auth_method": str(payload.get("token_endpoint_auth_method") or auth_method),
                "issuer": meta["issuer"],
                "registered_at": self._clock(),
            }
            self.secrets.set(CLIENT_KEY, json.dumps(record, separators=(",", ":")))
            log.info("Registered Escort with Notion (redirect %s)", NOTION_OAUTH_REDIRECT_URI)
            return record
        error = str(payload.get("error") or "")
        if response.status_code == 400 and (error in REDIRECT_REJECTED or "redirect" in str(payload.get("error_description") or "").lower()):
            raise ConnectorSetupError(
                PROVIDER,
                "Notion would not accept Escort's sign-in redirect. Use an integration token instead (Advanced).",
                code="redirect_rejected",
            )
        raise ConnectorError(PROVIDER, f"Notion refused to register Escort ({error or response.status_code})")

    # ---- browser flow ---------------------------------------------------------------

    def build_auth_url(self, meta: dict[str, Any], client: dict[str, Any], redirect_uri: str, state: str, challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": meta["resource"],
        }
        joiner = "&" if "?" in meta["authorization_endpoint"] else "?"
        return f"{meta['authorization_endpoint']}{joiner}{urllib.parse.urlencode(params)}"

    def begin(self, open_browser: bool = True, timeout_s: float = FLOW_TIMEOUT_S) -> str:
        """Discover, register once, open Notion's consent page; finish via escort:// callback."""
        meta = self.discover()
        client = self.ensure_client(meta)
        state = stdlib_secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        redirect_uri = NOTION_OAUTH_REDIRECT_URI
        with self._flow_lock:
            existing = self._flow
            if (
                existing is not None
                and not existing.finished.is_set()
                and existing.redirect_uri == redirect_uri
                and existing.client.get("client_id") == client.get("client_id")
            ):
                auth_url = self.build_auth_url(meta, client, redirect_uri, state, challenge)
                existing.verifiers[existing.state] = existing.verifier
                existing.verifiers[state] = verifier
                existing.state = state
                existing.verifier = verifier
                existing.auth_url = auth_url
                existing.client = client
                existing.meta = meta
                if existing.timer is not None:
                    existing.timer.cancel()
                timer = threading.Timer(timeout_s, self._expire, args=(existing,))
                timer.daemon = True
                timer.start()
                existing.timer = timer
                self.last_error = None
                flow = existing
            else:
                self._cancel_locked()
                auth_url = self.build_auth_url(meta, client, redirect_uri, state, challenge)
                flow = PendingFlow(
                    state=state,
                    verifier=verifier,
                    redirect_uri=redirect_uri,
                    auth_url=auth_url,
                    client=client,
                    meta=meta,
                    verifiers={state: verifier},
                )
                timer = threading.Timer(timeout_s, self._expire, args=(flow,))
                timer.daemon = True
                timer.start()
                flow.timer = timer
                self._flow = flow
                self.last_error = None
        if open_browser:
            try:
                self._open_browser(flow.auth_url)
            except Exception:
                pass
        self.on_change()
        return flow.auth_url

    def _expire(self, flow: PendingFlow) -> None:
        with self._flow_lock:
            if self._flow is flow and not flow.finished.is_set():
                self.last_error = "Notion sign-in timed out"
                self._finish_locked(flow)
                self.on_change()

    def cancel(self) -> None:
        with self._flow_lock:
            self._cancel_locked()

    def _cancel_locked(self) -> None:
        if self._flow is not None:
            self._finish_locked(self._flow)
            self._flow = None

    def _finish_locked(self, flow: PendingFlow) -> None:
        flow.finished.set()
        if flow.timer is not None:
            flow.timer.cancel()
            flow.timer = None

    def handle_callback(self, params: dict[str, str]) -> tuple[int, str]:
        with self._flow_lock:
            flow = self._flow
            if flow is None or flow.finished.is_set():
                log.warning("Notion callback received with no active sign-in flow")
                return 400, _page("Sign-in not in progress", "Start the Notion connection again from Escort.")
            state = params.get("state") or ""
            candidates = dict(flow.verifiers)
            candidates[flow.state] = flow.verifier
            matched_verifier: str | None = None
            if state:
                for candidate_state, candidate_verifier in candidates.items():
                    if hmac.compare_digest(state.encode(), candidate_state.encode()):
                        matched_verifier = candidate_verifier
                        break
            if matched_verifier is None:
                log.warning("Notion callback rejected: state mismatch")
                return 400, _page("Sign-in rejected", "The request did not match the sign-in Escort started (state mismatch).")
            issuer = params.get("iss")
            if issuer and issuer.rstrip("/") != str(flow.meta["issuer"]).rstrip("/"):
                log.warning("Notion callback rejected: unexpected issuer")
                return 400, _page("Sign-in rejected", "The response came from an unexpected server.")
            if params.get("error"):
                err_code = str(params["error"])
                self.last_error = f"Notion sign-in was not completed ({err_code})"
                self._finish_locked(flow)
                self.on_change()
                log.warning("Notion callback returned OAuth error: %s", err_code)
                return 400, _page("Notion sign-in was not completed", "You can close this tab and try again from Escort.")
            code = params.get("code") or ""
            if not code:
                log.warning("Notion callback rejected: missing authorization code")
                return 400, _page("Sign-in rejected", "No authorization code was returned.")
            flow.verifier = matched_verifier
            flow.finished.set()
            if flow.timer is not None:
                flow.timer.cancel()
                flow.timer = None
        try:
            self.exchange_code(code, flow)
        except ConnectorError as err:
            self.last_error = str(err)
            self.on_change()
            log.warning("Notion token exchange failed: %s", err)
            return 400, _page("Notion sign-in failed", "Escort could not finish connecting. You can close this tab and try again.")
        finally:
            with self._flow_lock:
                self._finish_locked(flow)
        return 200, _page("Escort is connected to Notion", "You can close this tab and return to Escort.")

    # ---- tokens ----------------------------------------------------------------------
    def _token_post(self, meta: dict[str, Any], client: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
        form = {**form, "client_id": client["client_id"]}
        auth = None
        secret = client.get("client_secret")
        if secret and client.get("token_endpoint_auth_method") == "client_secret_basic":
            auth = (client["client_id"], str(secret))
        elif secret:
            form["client_secret"] = str(secret)
        try:
            response = self.http.post(meta["token_endpoint"], data=form, headers={"Accept": "application/json"}, auth=auth)
        except httpx.HTTPError as err:
            raise ConnectorError(PROVIDER, f"Could not reach Notion: {err}") from err
        payload = _json(response)
        if response.status_code >= 400:
            error = str(payload.get("error") or response.status_code)
            if error in TERMINAL_ERRORS:
                if error == "invalid_client":
                    self.secrets.delete(CLIENT_KEY)  # the registration is gone: register again next time
                raise NotionReconnectError(PROVIDER, f"Notion ended Escort's access ({error}); reconnect Notion")
            raise ConnectorError(PROVIDER, f"Notion sign-in error ({error})")
        if not payload.get("access_token"):
            raise ConnectorError(PROVIDER, "Notion's token response had no access token")
        return payload

    def _record(self, payload: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        previous = previous or {}
        expires_in = payload.get("expires_in")
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": payload.get("refresh_token") or previous.get("refresh_token"),
            "expires_at": self._clock() + float(expires_in) if expires_in else None,
            "account": payload.get("workspace_name") or previous.get("account"),
        }

    def exchange_code(self, code: str, flow: PendingFlow) -> dict[str, Any]:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow.redirect_uri,
            "code_verifier": flow.verifier,
            "resource": flow.meta["resource"],
        }
        with self._token_lock:
            payload = self._token_post(flow.meta, flow.client, form)
            data = self._record(payload, previous=None)
            self._save_tokens(data)
        self._reset_session()
        self.last_error = None
        if not data.get("account"):
            name = self._workspace_name()
            if name:
                with self._token_lock:
                    current = self._tokens() or data
                    self._save_tokens({**current, "account": name})
        log.info("Connected Notion through MCP")
        self.on_change()
        try:
            self.on_connected()
        except Exception:
            pass
        return data

    def access_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            data = self._tokens()  # read inside the lock: another thread may have just rotated it
            if data is None:
                if self.needs_reconnect():
                    raise NotionReconnectError(PROVIDER, "Notion access ended; reconnect Notion")
                raise ConnectorAuthError(PROVIDER, "Notion is not connected", code="not_connected")
            expires_at = data.get("expires_at")
            fresh = expires_at is None or float(expires_at) - REFRESH_SKEW_S > self._clock()
            if fresh and not force_refresh and data.get("access_token"):
                return str(data["access_token"])
            if not data.get("refresh_token"):
                self._mark_reconnect("Notion session expired; reconnect Notion")
                raise NotionReconnectError(PROVIDER, "Notion session expired; reconnect Notion")
            meta = self.discover()
            client = self.client_registration()
            if not client:
                self._mark_reconnect("Escort's Notion registration is missing; reconnect Notion")
                raise NotionReconnectError(PROVIDER, "Reconnect Notion")
            form = {"grant_type": "refresh_token", "refresh_token": str(data["refresh_token"]), "resource": meta["resource"]}
            try:
                payload = self._token_post(meta, client, form)
            except NotionReconnectError:
                self._mark_reconnect("Notion access was revoked or expired; reconnect Notion")
                raise
            updated = self._record(payload, previous=data)
            self._save_tokens(updated)
            return str(updated["access_token"])

    def disconnect(self) -> None:
        self.cancel()
        data = self._tokens()
        meta = self._meta
        client = self.client_registration()
        token = (data or {}).get("refresh_token")
        if token and meta and meta.get("revocation_endpoint") and client:
            try:
                self.http.post(
                    str(meta["revocation_endpoint"]),
                    data={"token": str(token), "token_type_hint": "refresh_token", "client_id": client["client_id"]},
                )
            except httpx.HTTPError:
                pass
        # The client registration is kept and reused: registering again would orphan grants.
        self.secrets.delete(TOKEN_KEY)
        self._reset_session()
        self.last_error = None
        self.on_change()

    # ---- MCP over streamable HTTP ---------------------------------------------------
    def _next_id(self) -> int:
        with self._session_lock:
            self._rpc_id += 1
            return self._rpc_id

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(2):
            token = self.access_token(force_refresh=attempt > 0)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
            if self._protocol:
                headers["MCP-Protocol-Version"] = self._protocol
            try:
                response = self.http.post(self.mcp_url, json=body, headers=headers)
            except httpx.HTTPError as err:
                raise ConnectorError(PROVIDER, f"Could not reach Notion: {err}") from err
            if response.status_code != 401:
                return response
        self._mark_reconnect("Notion rejected Escort's access; reconnect Notion")
        raise NotionReconnectError(PROVIDER, "Notion rejected Escort's access; reconnect Notion")

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        rpc_id = None
        if not notify:
            rpc_id = self._next_id()
            body["id"] = rpc_id
        response = self._post(body)
        if response.status_code == 404 and self._session_id and method != "initialize":
            raise _SessionExpired()
        if response.status_code >= 400:
            raise ConnectorError(PROVIDER, f"Notion MCP error {response.status_code}")
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if notify:
            return None
        message = _read_message(response, rpc_id)
        if message is None:
            raise ConnectorError(PROVIDER, "Notion MCP sent no answer")
        if message.get("error"):
            error = message["error"] if isinstance(message["error"], dict) else {"message": str(message["error"])}
            raise ConnectorError(PROVIDER, f"Notion MCP error: {error.get('message') or error.get('code')}")
        return message.get("result") or {}

    def _ensure_session(self) -> None:
        with self._session_lock:
            if self._initialized:
                return
            result = self._rpc(
                "initialize",
                {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}},
            )
            self._protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)
            self._rpc("notifications/initialized", {}, notify=True)
            self._initialized = True

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(2):
            self._ensure_session()
            try:
                return self._rpc(method, params)
            except _SessionExpired:
                self._reset_session()
                if attempt:
                    raise ConnectorError(PROVIDER, "Notion MCP session could not be restarted") from None
        raise ConnectorError(PROVIDER, "Notion MCP session could not be restarted")

    def tools(self) -> list[dict[str, Any]]:
        """tools/list, cached for the session."""
        if self._tools is not None:
            return self._tools
        found: list[dict[str, Any]] = []
        cursor = None
        for _page_no in range(20):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            found += [tool for tool in result.get("tools") or [] if isinstance(tool, dict) and tool.get("name")]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._tools = found
        return found

    def tool(self, capability: str, required: bool = True) -> dict[str, Any] | None:
        preferred, words, banned = TOOL_HINTS[capability]
        tools = self.tools()
        by_name = {str(tool["name"]): tool for tool in tools}
        for name in preferred:
            if name in by_name:
                return by_name[name]
        for tool in tools:
            name = str(tool["name"]).lower()
            if all(word in name for word in words) and not any(word in name for word in banned):
                return tool
        if required:
            raise ConnectorError(PROVIDER, f"Notion MCP does not offer a tool to {TOOL_LABELS[capability]}", code="tool_missing")
        return None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            text = _result_text(result)[:300]
            lowered = text.lower()
            code = "not_found" if ("not found" in lowered or "could not find" in lowered or "object_not_found" in lowered) else None
            raise ConnectorError(PROVIDER, f"Notion: {text or 'the tool call failed'}", code=code)
        return _result_payload(result)

    # ---- capabilities ---------------------------------------------------------------
    def _workspace_name(self) -> str | None:
        try:
            tool = self.tool("self", required=False)
            if tool is None:
                return None
            payload = self.call_tool(str(tool["name"]), {})
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        workspace = payload.get("workspace")
        if isinstance(workspace, dict) and workspace.get("name"):
            return str(workspace["name"])
        if isinstance(workspace, str) and workspace:
            return workspace
        bot = payload.get("bot")
        if isinstance(bot, dict) and bot.get("workspace_name"):
            return str(bot["workspace_name"])
        if payload.get("workspace_name"):
            return str(payload["workspace_name"])
        user = payload.get("user")
        if isinstance(user, dict) and user.get("name"):
            return str(user["name"])
        if payload.get("name"):
            return str(payload["name"])
        return None

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        tool = self.tool("search")
        props = _props(tool.get("inputSchema"))
        args: dict[str, Any] = {"query": query.strip()}
        if "query_type" in props:
            args["query_type"] = "internal"
        for key in ("page_size", "limit", "max_results"):
            if key in props:
                args[key] = max(1, min(limit, 50))
                break
        payload = self.call_tool(str(tool["name"]), args)
        results = payload.get("results") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        hits = []
        for row in results or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            kind = str(row.get("type") or row.get("object") or "page")
            if kind not in ("page",):
                continue
            stamp = str(row.get("timestamp") or row.get("last_edited_time") or "")[:10]
            snippet = str(row.get("highlight") or row.get("snippet") or "").strip()
            hits.append(
                {
                    "id": str(row["id"]),
                    "title": str(row.get("title") or "Untitled"),
                    "snippet": snippet[:200] if snippet else (f"Notion page · edited {stamp}" if stamp else "Notion page"),
                    "url": row.get("url"),
                    "mock": False,
                }
            )
        return hits[:limit]

    def fetch_markdown(self, page_id: str) -> tuple[str, str]:
        """(title, page body as markdown) through the fetch tool."""
        tool = self.tool("fetch")
        props = _props(tool.get("inputSchema"))
        key = "id" if "id" in props or not props else "url" if "url" in props else next(iter(_required(tool.get("inputSchema"))), "id")
        payload = self.call_tool(str(tool["name"]), {key: page_id})
        return _page_markdown(payload)

    def fetch(self, page_id: str) -> ContextItem:
        title, body = self.fetch_markdown(page_id)
        return ContextItem(
            id=new_id("ctx"),
            source_kind="notion",
            source_label="Notion",
            title=title,
            sections=sections_from_markdown(f"# {title}\n\n{body}"),
            enabled=True,
            external_id=page_id,
        )

    def create_page(self, parent_id: str, title: str, content: str = "") -> dict[str, Any]:
        tool = self.tool("create_page")
        schema = tool.get("inputSchema") or {}
        parent: dict[str, Any] = {"page_id": parent_id}
        parent_schema = _props(schema).get("parent") or {}
        if "type" in _required(parent_schema) or "type" in _props(parent_schema):
            parent["type"] = "page_id"
        page: dict[str, Any] = {"properties": {"title": title}}
        if content:
            page["content"] = content
        payload = self.call_tool(str(tool["name"]), {"parent": parent, "pages": [page]})
        pages = payload.get("pages") if isinstance(payload, dict) else None
        created = pages[0] if isinstance(pages, list) and pages and isinstance(pages[0], dict) else payload if isinstance(payload, dict) else {}
        if not created.get("id"):
            raise ConnectorError(PROVIDER, "Notion did not return the new page")
        return {"id": str(created["id"]), "url": str(created.get("url") or "")}

    # Page edits. Every edit names text that is on the page right now (read just before), so it
    # can only touch the line the user saw in the preview.
    def _update_tool(self) -> tuple[str, bool, set[str], str]:
        tool = self.tool("update_page")
        schema = tool.get("inputSchema") or {}
        props = _props(schema)
        wrapped = "data" in props and "page_id" not in props
        inner = props["data"] if wrapped else schema
        commands = _commands(inner)
        ops_field = _ops_field(inner) or "content_updates"
        return str(tool["name"]), wrapped, commands, ops_field

    def _update(self, page_id: str, args: dict[str, Any]) -> None:
        name, wrapped, _commands_, _field = self._update_tool()
        body = {"page_id": page_id, **args}
        self.call_tool(name, {"data": body} if wrapped else body)

    def replace_span(self, page_id: str, old: str, new: str) -> None:
        _name, _wrapped, commands, ops_field = self._update_tool()
        if "update_content" in commands or not commands:
            self._update(page_id, {"command": "update_content", ops_field: [{"old_str": old, "new_str": new}]})
        elif "replace_content_range" in commands:
            self._update(page_id, {"command": "replace_content_range", "selection_with_ellipsis": ellipsis(old), "new_str": new})
        else:
            raise ConnectorError(PROVIDER, "Notion MCP cannot edit page text here", code="tool_missing")

    def insert_after(self, page_id: str, anchor: str, markdown: str) -> None:
        _name, _wrapped, commands, _field = self._update_tool()
        if "insert_content_after" in commands:
            self._update(page_id, {"command": "insert_content_after", "selection_with_ellipsis": ellipsis(anchor), "new_str": markdown})
        else:
            self.replace_span(page_id, anchor, f"{anchor}\n{markdown}")

    def write_empty(self, page_id: str, markdown: str) -> None:
        _name, _wrapped, commands, _field = self._update_tool()
        if "replace_content" not in commands and commands:
            raise ConnectorError(PROVIDER, "Notion MCP cannot add content to an empty page here", code="tool_missing")
        self._update(page_id, {"command": "replace_content", "new_str": markdown})


class _SessionExpired(Exception):
    pass


# ---- helpers ------------------------------------------------------------------------
def _read_message(response: httpx.Response, rpc_id: int | None) -> dict[str, Any] | None:
    kind = response.headers.get("content-type", "")
    if "text/event-stream" in kind:
        data_lines: list[str] = []
        for line in [*response.text.splitlines(), ""]:
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line.strip() and data_lines:
                try:
                    message = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    message = None
                data_lines = []
                for item in message if isinstance(message, list) else [message]:
                    if isinstance(item, dict) and item.get("id") == rpc_id and ("result" in item or "error" in item):
                        return item
        return None
    data = _json(response) if response.content else {}
    return data or None


def _result_text(result: dict[str, Any]) -> str:
    return "\n".join(str(part.get("text") or "") for part in result.get("content") or [] if isinstance(part, dict) and part.get("type") == "text")


def _result_payload(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)) and structured:
        return structured
    text = _result_text(result)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"text": text}


def _props(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _required(schema: Any) -> list[str]:
    return list(schema.get("required") or []) if isinstance(schema, dict) else []


def _walk(schema: Any):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _walk(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk(value)


def _commands(schema: Any) -> set[str]:
    """Every value the update tool's `command` argument may take, across oneOf/anyOf variants."""
    found: set[str] = set()
    for node in _walk(schema):
        command = _props(node).get("command")
        if isinstance(command, dict):
            if command.get("const"):
                found.add(str(command["const"]))
            found.update(str(value) for value in command.get("enum") or [])
    return found


def _ops_field(schema: Any) -> str | None:
    """Name of the array argument whose items carry old_str/new_str (update_content)."""
    for node in _walk(schema):
        for name, prop in _props(node).items():
            if isinstance(prop, dict) and prop.get("type") == "array" and "old_str" in _props(prop.get("items")):
                return str(name)
    return None


def ellipsis(text: str, edge: int = 12) -> str:
    """Notion's selection_with_ellipsis: the start and end of a unique span."""
    if len(text) <= edge * 2:
        half = max(1, len(text) // 2)
        return f"{text[:half]}...{text[half:]}"
    return f"{text[:edge]}...{text[-edge:]}"


_CONTENT = re.compile(r"<content>\n?(.*?)\n?</content>", re.S)


def _page_markdown(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "Untitled", str(payload or "")
    title = str(payload.get("title") or "").strip()
    text = str(payload.get("text") or payload.get("markdown") or payload.get("content") or "")
    match = _CONTENT.search(text)
    body = match.group(1) if match else text
    if not title:
        found = re.search(r'title="([^"]*)"', text) or re.search(r"^#\s+(.+)$", body, re.M)
        title = found.group(1).strip() if found else "Untitled"
    return title or "Untitled", body.strip("\n")


_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*]\s+\[[ xX]\]\s+|[-*]\s+|\d+\.\s+|>\s+)?)")
_ATTRS = re.compile(r"\s*\{[^{}]*\}\s*$")


def line_kind(line: str) -> tuple[str, int]:
    stripped = line.strip()
    for marks, kind in (("### ", "heading_3"), ("## ", "heading_2"), ("# ", "heading_1")):
        if stripped.startswith(marks):
            return kind, len(marks) - 1
    if re.match(r"^[-*]\s+\[[ xX]\]\s", stripped):
        return "to_do", 0
    if re.match(r"^[-*]\s", stripped):
        return "bulleted_list_item", 0
    if re.match(r"^\d+\.\s", stripped):
        return "numbered_list_item", 0
    if stripped.startswith(">"):
        return "quote", 0
    if stripped.startswith("<") or stripped.startswith("```") or stripped.startswith("|") or stripped == "---":
        return "unsupported", 0
    return "paragraph", 0


def line_text(line: str) -> str:
    return _ATTRS.sub("", line[len(_PREFIX.match(line).group(1)) :]).strip()


def line_id(index: int, line: str) -> str:
    return f"md{index}-{hashlib.sha1(line.encode('utf-8')).hexdigest()[:10]}"


def markdown_blocks(body: str) -> tuple[list[str], list[dict[str, Any]]]:
    """The fetched page as lines, plus one pseudo-block per non-blank line (same shape as REST blocks).

    Ids hash the line and its position, so a plan made from a stale read never matches.
    """
    lines = body.split("\n")
    blocks = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        kind, level = line_kind(line)
        blocks.append({"id": line_id(index, line), "type": kind, "text": line_text(line), "line": line.strip(), "level": level, "index": index})
    return lines, blocks


def unique_span(lines: list[str], index: int) -> tuple[int, str]:
    """Grow the span upward from line `index` until it appears once on the page (Notion needs that)."""
    body = "\n".join(lines)
    start = index
    span = lines[index]
    while body.count(span) > 1 and start > 0:
        start -= 1
        span = "\n".join(lines[start : index + 1])
    return start, span


def replace_prefix(line: str, text: str) -> str:
    return f"{_PREFIX.match(line).group(1)}{text}"


def block_markdown(item: dict[str, Any]) -> str:
    kind = item.get("type") or ""
    data = item.get(kind) or {}
    text = "".join(str((part.get("text") or {}).get("content") or "") for part in data.get("rich_text") or [])
    if kind == "heading_1":
        return f"# {text}"
    if kind == "heading_2":
        return f"## {text}"
    if kind == "heading_3":
        return f"### {text}"
    if kind == "bulleted_list_item":
        return f"- {text}"
    if kind == "numbered_list_item":
        return f"1. {text}"
    if kind == "to_do":
        return f"- [{'x' if data.get('checked') else ' '}] {text}"
    if kind == "quote":
        return f"> {text}"
    return text


def blocks_markdown(children: list[dict[str, Any]]) -> str:
    return "\n".join(block_markdown(item) for item in children)


