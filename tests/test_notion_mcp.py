"""Notion one-click connect over MCP: discovery, DCR reuse, PKCE/state, token rotation, tools (no network)."""

import base64
import hashlib
import json
import logging
import threading
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources.base import ConnectorSetupError
from escort.context.sources.notion_mcp import (
    CLIENT_KEY,
    NOTION_OAUTH_REDIRECT_URI,
    TOKEN_KEY,
    NotionMcp,
    NotionReconnectError,
    markdown_blocks,
    notion_oauth_callback_params,
)
from escort.context.sources.notion_source import TOKEN_KEY as REST_TOKEN_KEY
from escort.context.sources.notion_write import NotionNotes, NotionNotesState, UnknownPageError, UpdatePlans
from escort.context.sources.registry import Connectors, build_sources
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore

ORIGIN = "https://mcp.notion.test"
MCP = f"{ORIGIN}/mcp"
PARENT = "1111aaaa-2222-bbbb-3333-cccc4444dddd"
TARGET = "9999aaaa-2222-bbbb-3333-cccc4444eeee"
REST_TOKEN = "ntn_rest_integration_token"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ESCORT_CONNECTORS", raising=False)


NEW_TOOLS = [
    {"name": "notion-search", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "query_type": {"type": "string"}}, "required": ["query"]}},
    {"name": "notion-fetch", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {
        "name": "notion-create-pages",
        "inputSchema": {"type": "object", "properties": {"parent": {"type": "object", "properties": {"page_id": {"type": "string"}}}, "pages": {"type": "array"}}},
    },
    {
        "name": "notion-update-page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "command": {"type": "string", "enum": ["update_properties", "update_content", "replace_content"]},
                "content_updates": {"type": "array", "items": {"type": "object", "properties": {"old_str": {"type": "string"}, "new_str": {"type": "string"}}}},
                "new_str": {"type": "string"},
            },
        },
    },
    {"name": "notion-get-self", "inputSchema": {"type": "object", "properties": {}}},
]

# An older server: different names, arguments wrapped in `data`, selection_with_ellipsis commands.
OLD_TOOLS = [
    {"name": "workspace-search", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "fetch-page", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}}},
    {"name": "create-page-batch", "inputSchema": {"type": "object", "properties": {"parent": {}, "pages": {}}}},
    {
        "name": "page-update",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "oneOf": [
                        {"properties": {"page_id": {}, "command": {"const": "insert_content_after"}, "selection_with_ellipsis": {}, "new_str": {}}},
                        {"properties": {"page_id": {}, "command": {"const": "replace_content_range"}, "selection_with_ellipsis": {}, "new_str": {}}},
                        {"properties": {"page_id": {}, "command": {"const": "replace_content"}, "new_str": {}}},
                    ],
                }
            },
        },
    },
]


def _span(md: str, selection: str) -> tuple[int, int] | None:
    start_part, end_part = selection.split("...", 1)
    starts = [i for i in range(len(md)) if md.startswith(start_part, i)]
    found = []
    for s in starts:
        e = md.find(end_part, s + len(start_part))
        if e >= 0:
            found.append((s, e + len(end_part)))
    return found[0] if len(found) == 1 else None


class FakeNotion:
    """Notion's OAuth endpoints and its MCP server, in memory."""

    def __init__(self, tools=None, reject_hosts=(), reject_redirects=()) -> None:
        self.tools = tools or NEW_TOOLS
        self.reject_hosts = set(reject_hosts)
        self.reject_redirects = set(reject_redirects)
        self.requests: list[httpx.Request] = []
        self.registrations: list[dict] = []
        self.token_forms: list[dict] = []
        self.challenge: str | None = None
        self.codes: dict[str, str] = {}
        self.access: set[str] = set()
        self.refresh: set[str] = set()
        self.counter = 0
        self.revoked = False
        self.tools_list_calls = 0
        self.calls: list[tuple[str, dict]] = []
        self.sessions: set[str] = set()
        self.expire_next = False
        self.pages: dict[str, dict] = {
            PARENT: {"title": "Meetings", "md": "Meeting pages live here."},
            TARGET: {"title": "CRM notes", "md": "# CRM notes\n## Commitments\n- Old item\n## Notes\nPhase 2 waits for KYC."},
        }

    def mcp_calls(self, name_part: str) -> list[dict]:
        return [args for name, args in self.calls if name_part in name]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [ORIGIN], "scopes_supported": ["default"]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ORIGIN,
                    "authorization_endpoint": f"{ORIGIN}/authorize",
                    "token_endpoint": f"{ORIGIN}/token",
                    "registration_endpoint": f"{ORIGIN}/register",
                    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if path == "/register":
            body = json.loads(request.content)
            self.registrations.append(body)
            if set(body["redirect_uris"]) & self.reject_redirects:
                return httpx.Response(400, json={"error": "invalid_redirect_uri"})
            hosts = {urllib.parse.urlsplit(uri).hostname for uri in body["redirect_uris"] if urllib.parse.urlsplit(uri).scheme == "http"}
            if hosts & self.reject_hosts:
                return httpx.Response(400, json={"error": "invalid_redirect_uri"})
            return httpx.Response(201, json={**body, "client_id": f"client-{len(self.registrations)}"})
        if path == "/token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.token_forms.append(form)
            if form["grant_type"] == "authorization_code":
                verifier = form["code_verifier"]
                digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if self.codes.get(form["code"]) != digest:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return self._issue()
            if self.revoked or form["refresh_token"] not in self.refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refresh.discard(form["refresh_token"])  # rotation: the old refresh token is retired
            return self._issue()
        if path == "/mcp":
            return self._mcp(request)
        return httpx.Response(404)

    def _issue(self) -> httpx.Response:
        self.counter += 1
        access, refresh = f"at-{self.counter}", f"rt-{self.counter}"
        self.access.add(access)
        self.refresh.add(refresh)
        return httpx.Response(200, json={"access_token": access, "refresh_token": refresh, "expires_in": 28800, "token_type": "bearer"})

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.access:
            return httpx.Response(401)
        body = json.loads(request.content)
        method = body["method"]
        if method == "initialize":
            sid = f"sess-{len(self.sessions) + 1}"
            self.sessions.add(sid)
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}, headers={"Mcp-Session-Id": sid})
        sid = request.headers.get("Mcp-Session-Id")
        if sid not in self.sessions:
            return httpx.Response(400)
        if self.expire_next:
            self.expire_next = False
            self.sessions.discard(sid)
            return httpx.Response(404)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            self.tools_list_calls += 1
            message = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": self.tools}})
            # Streamable HTTP may answer as an SSE stream.
            return httpx.Response(200, text=f"event: message\ndata: {message}\n\n", headers={"Content-Type": "text/event-stream"})
        if method == "tools/call":
            name, args = body["params"]["name"], body["params"]["arguments"]
            self.calls.append((name, args))
            payload, error = self._tool(name, args.get("data", args))
            result = {"content": [{"type": "text", "text": payload if error else json.dumps(payload)}], "isError": error}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "no such method"}})

    def _tool(self, name: str, args: dict) -> tuple:
        if "self" in name:
            return {"workspace": {"name": "Acme HQ"}, "user": {"name": "Pia"}}, False
        if "search" in name:
            q = args.get("query", "").lower()
            return {"results": [{"id": pid, "title": p["title"], "url": f"https://notion.so/{pid}", "type": "page", "highlight": p["md"][:40], "timestamp": "2026-10-31T00:00:00Z"} for pid, p in self.pages.items() if q in p["title"].lower()]}, False
        if "fetch" in name:
            page = self.pages.get(args["id"])
            if page is None:
                return "Could not find page with ID", True
            text = f'<page url="https://notion.so/{args["id"]}">\n<properties>\n{{"title":"{page["title"]}"}}\n</properties>\n<content>\n{page["md"]}\n</content>\n</page>'
            return {"metadata": {"type": "page"}, "title": page["title"], "url": f"https://notion.so/{args['id']}", "text": text}, False
        if "create" in name:
            if args["parent"]["page_id"] != PARENT:
                return "Could not find parent page", True
            created = []
            for page in args["pages"]:
                self.counter += 1
                pid = f"page-{self.counter}"
                self.pages[pid] = {"title": page["properties"]["title"], "md": page.get("content", "")}
                created.append({"id": pid, "url": f"https://notion.so/{pid}", "properties": page["properties"]})
            return {"pages": created}, False
        if "update" in name:
            page = self.pages.get(args["page_id"])
            if page is None:
                return "Could not find page", True
            command = args["command"]
            if command == "update_content":
                for op in args["content_updates"]:
                    if page["md"].count(op["old_str"]) != 1:
                        return "old_str must match exactly one location", True
                    page["md"] = page["md"].replace(op["old_str"], op["new_str"])
            elif command == "replace_content":
                page["md"] = args["new_str"]
            elif command in ("insert_content_after", "replace_content_range"):
                span = _span(page["md"], args["selection_with_ellipsis"])
                if span is None:
                    return "selection not unique", True
                s, e = span
                if command == "insert_content_after":
                    page["md"] = page["md"][:e] + "\n" + args["new_str"] + page["md"][e:]
                else:
                    page["md"] = page["md"][:s] + args["new_str"] + page["md"][e:]
            return {"page_id": args["page_id"]}, False
        return "unknown tool", True


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _mcp(fake=None, secrets=None, clock=None):
    fake = fake or FakeNotion()
    secrets = secrets if secrets is not None else MemorySecretStore()
    opened: list[str] = []
    http = httpx.Client(transport=httpx.MockTransport(fake))
    mcp = NotionMcp(secrets, http, mcp_url=MCP, open_browser=opened.append, clock=clock or Clock())
    return mcp, fake, secrets, opened


def _params(url: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _connect(mcp: NotionMcp, fake: FakeNotion) -> dict:
    url = mcp.begin()
    params = _params(url)
    fake.codes["good-code"] = params["code_challenge"]
    status, _html = mcp.handle_callback({"code": "good-code", "state": params["state"]})
    assert status == 200
    return params


@pytest.fixture
def connected():
    mcp, fake, secrets, opened = _mcp()
    _connect(mcp, fake)
    yield mcp, fake, secrets
    mcp.cancel()


# ---- discovery, registration, PKCE + state ------------------------------------------


def test_discovery_reads_protected_resource_then_authorization_server():
    mcp, _fake, _secrets, _opened = _mcp()
    meta = mcp.discover()
    assert meta["resource"] == MCP and meta["issuer"] == ORIGIN
    assert meta["authorization_endpoint"] == f"{ORIGIN}/authorize" and meta["token_endpoint"] == f"{ORIGIN}/token"
    assert meta["registration_endpoint"] == f"{ORIGIN}/register"


def test_auth_url_has_pkce_state_consent_and_custom_redirect():
    mcp, fake, _secrets, opened = _mcp()
    try:
        url = mcp.begin()
        assert opened == [url] and url.startswith(f"{ORIGIN}/authorize?")
        params = _params(url)
        assert params["response_type"] == "code" and params["client_id"] == "client-1"
        assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
        assert params["resource"] == MCP and len(params["state"]) >= 40
        assert params["redirect_uri"] == NOTION_OAUTH_REDIRECT_URI
        reg = fake.registrations[0]
        assert reg["client_name"] == "Escort" and reg["token_endpoint_auth_method"] == "none"
        assert reg["grant_types"] == ["authorization_code", "refresh_token"] and reg["response_types"] == ["code"]
        assert reg["redirect_uris"] == [NOTION_OAUTH_REDIRECT_URI]
        assert mcp.pending()
    finally:
        mcp.cancel()


def test_notion_oauth_callback_params_parses_escort_url():
    assert notion_oauth_callback_params("escort://oauth/notion?code=c&state=s") == {"code": "c", "state": "s"}
    assert notion_oauth_callback_params("https://evil.test/cb?code=c") is None


def test_client_registration_is_persisted_and_reused():
    secrets = MemorySecretStore()
    mcp, fake, _s, _o = _mcp(secrets=secrets)
    _connect(mcp, fake)
    mcp.disconnect()
    assert secrets.get(TOKEN_KEY) is None and json.loads(secrets.get(CLIENT_KEY))["client_id"] == "client-1"
    _connect(mcp, fake)  # second connect, same process
    again, _f, _s2, _o2 = _mcp(fake=fake, secrets=secrets)  # and after a restart
    url = again.begin()
    again.cancel()
    assert len(fake.registrations) == 1 and _params(url)["client_id"] == "client-1"


def test_state_mismatch_and_missing_flow_are_rejected_without_token_exchange():
    mcp, fake, _secrets, _opened = _mcp()
    assert mcp.handle_callback({"code": "x", "state": "y"})[0] == 400  # nothing pending
    try:
        url = mcp.begin()
        status, html = mcp.handle_callback({"code": "good-code", "state": "forged"})
        assert status == 400 and "state mismatch" in html
        status, _ = mcp.handle_callback({"code": "good-code", "state": _params(url)["state"], "iss": "https://evil.test"})
        assert status == 400
        assert fake.token_forms == [] and mcp.pending() and not mcp.connected()  # the real flow survives
    finally:
        mcp.cancel()


def test_code_exchange_sends_verifier_and_stores_tokens_with_workspace(connected):
    mcp, fake, secrets = connected
    form = fake.token_forms[0]
    assert form["grant_type"] == "authorization_code" and form["code"] == "good-code" and form["client_id"] == "client-1"
    assert form["resource"] == MCP and form["redirect_uri"] == NOTION_OAUTH_REDIRECT_URI and form["code_verifier"]
    stored = json.loads(secrets.get(TOKEN_KEY))
    assert stored["access_token"] == "at-1" and stored["refresh_token"] == "rt-1" and stored["expires_at"] == 1_000_000.0 + 28800
    assert mcp.connected() and mcp.account() == "Acme HQ" and not mcp.pending()


def test_wrong_verifier_is_refused():
    mcp, fake, _secrets, _opened = _mcp()
    url = mcp.begin()
    fake.codes["good-code"] = "not-the-challenge"
    status, _ = mcp.handle_callback({"code": "good-code", "state": _params(url)["state"]})
    assert status == 400 and not mcp.connected()


def test_escort_redirect_url_completes_connection():
    mcp, fake, _secrets, _opened = _mcp()
    url = mcp.begin()
    params = _params(url)
    fake.codes["c1"] = params["code_challenge"]
    callback = f"escort://oauth/notion?code=c1&state={params['state']}"
    status, html = mcp.handle_callback(notion_oauth_callback_params(callback) or {})
    assert status == 200 and "connected to Notion" in html
    assert mcp.connected()


def test_repeated_begin_keeps_flow_and_accepts_first_tab_callback():
    mcp, fake, _secrets, _opened = _mcp()
    url1 = mcp.begin()
    params1 = _params(url1)
    url2 = mcp.begin()
    params2 = _params(url2)
    assert params1["redirect_uri"] == params2["redirect_uri"] == NOTION_OAUTH_REDIRECT_URI
    assert params1["state"] != params2["state"]
    fake.codes["c_first"] = params1["code_challenge"]
    status, html = mcp.handle_callback({"code": "c_first", "state": params1["state"]})
    assert status == 200 and "connected to Notion" in html
    assert mcp.connected()


def test_stale_loopback_registration_is_reregistered():
    secrets = MemorySecretStore()
    secrets.set(
        CLIENT_KEY,
        json.dumps(
            {
                "client_id": "stale",
                "issuer": ORIGIN,
                "redirect_uris": ["http://127.0.0.1:9911/callback"],
                "token_endpoint_auth_method": "none",
            }
        ),
    )
    mcp, fake, _s, _o = _mcp(secrets=secrets)
    url = mcp.begin()
    mcp.cancel()
    assert _params(url)["redirect_uri"] == NOTION_OAUTH_REDIRECT_URI
    assert json.loads(secrets.get(CLIENT_KEY))["client_id"] == "client-1"
    assert fake.registrations[0]["redirect_uris"] == [NOTION_OAUTH_REDIRECT_URI]


def test_redirect_rejection_keeps_integration_token():
    fake = FakeNotion(reject_redirects={NOTION_OAUTH_REDIRECT_URI})
    secrets = MemorySecretStore()
    secrets.set(REST_TOKEN_KEY, json.dumps({"token": REST_TOKEN, "account": "Acme"}))
    connectors = Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(fake)), open_browser=lambda _u: None)
    connectors.notion_mcp.mcp_url = MCP
    with pytest.raises(ConnectorSetupError) as err:
        connectors.connect_notion_oauth()
    assert err.value.code == "redirect_rejected" and "integration token" in str(err.value)
    assert secrets.get(REST_TOKEN_KEY) and connectors.notion_auth() == "token"


# ---- tokens: rotation, lock, invalid_grant -------------------------------------------


def test_refresh_rotates_and_persists_both_tokens_atomically(connected):
    mcp, fake, secrets = connected
    writes: list[dict] = []
    original = secrets.set
    secrets.set = lambda name, value: (writes.append(json.loads(value)) if name == TOKEN_KEY else None, original(name, value))[1]
    mcp._clock.now += 28800 - 100  # inside the refresh window
    assert mcp.access_token() == "at-2"
    assert writes == [{"access_token": "at-2", "refresh_token": "rt-2", "expires_at": mcp._clock.now + 28800, "account": "Acme HQ"}]
    mcp._clock.now += 28800
    assert mcp.access_token() == "at-3"
    assert [f["refresh_token"] for f in fake.token_forms if f["grant_type"] == "refresh_token"] == ["rt-1", "rt-2"]


def test_concurrent_refreshes_are_serialized(connected):
    mcp, fake, _secrets = connected
    mcp._clock.now += 28800
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(mcp.access_token())) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(results) == {"at-2"}
    assert len([f for f in fake.token_forms if f["grant_type"] == "refresh_token"]) == 1


def test_invalid_grant_is_terminal_and_asks_to_reconnect(connected):
    mcp, fake, secrets = connected
    fake.revoked = True
    mcp._clock.now += 28800
    with pytest.raises(NotionReconnectError) as err:
        mcp.access_token()
    assert err.value.code == "needs_reconnect"
    assert not mcp.connected() and mcp.needs_reconnect() and mcp.account() == "Acme HQ"
    assert "rt-1" not in (secrets.get(TOKEN_KEY) or "")
    refreshes = len([f for f in fake.token_forms if f["grant_type"] == "refresh_token"])
    with pytest.raises(NotionReconnectError):
        mcp.access_token()
    assert len([f for f in fake.token_forms if f["grant_type"] == "refresh_token"]) == refreshes  # no retry


def test_status_row_shows_auth_and_reconnect_without_secrets(tmp_path: Path, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeNotion()
    secrets = MemorySecretStore()
    connectors = Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(fake)), open_browser=lambda _u: None)
    connectors.notion_mcp.mcp_url = MCP
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors, monitor_lister=lambda: [])
    client = TestClient(create_desktop_app(runtime))
    started = client.post("/api/connectors/notion/connect")
    assert started.status_code == 200 and set(started.json()) == {"auth_url"}
    params = _params(started.json()["auth_url"])
    row = client.get("/api/connectors/status").json()["providers"][1]
    assert row["pending"] is True and row["connected"] is False and row["auth"] is None
    fake.codes["good-code"] = params["code_challenge"]
    assert connectors.notion_mcp.handle_callback({"code": "good-code", "state": params["state"]})[0] == 200
    row = client.get("/api/connectors/status").json()["providers"][1]
    assert row["auth"] == "oauth" and row["connected"] is True and row["account"] == "Acme HQ" and row["needs_reconnect"] is False
    full = client.get("/api/status").text
    for secret in ("at-1", "rt-1", params["code_challenge"], params["state"]):
        assert secret not in full and secret not in caplog.text
    assert not (tmp_path / "config.json").exists() or "rt-1" not in (tmp_path / "config.json").read_text()

    fake.revoked = True
    connectors.notion_mcp._clock = lambda: 10**12
    assert client.post("/api/context/search", json={"source": "notion", "query": "crm"}).status_code == 401
    row = client.get("/api/connectors/status").json()["providers"][1]
    assert row["needs_reconnect"] is True and row["connected"] is False and row["reconnect_account"] == "Acme HQ"
    assert "rt-" not in client.get("/api/status").text and "rt-" not in caplog.text

    client.post("/api/connectors/notion/disconnect")
    assert secrets.get(TOKEN_KEY) is None and secrets.get(CLIENT_KEY)  # registration kept for reuse


# ---- tools/list-driven mapping -------------------------------------------------------


def test_search_and_fetch_go_through_discovered_tools(connected):
    mcp, fake, _secrets = connected
    hits = mcp.search("crm")
    assert hits == [{"id": TARGET, "title": "CRM notes", "snippet": "# CRM notes\n## Commitments\n- Old item\n##", "url": f"https://notion.so/{TARGET}", "mock": False}]
    assert fake.mcp_calls("notion-search")[0] == {"query": "crm", "query_type": "internal"}
    item = mcp.fetch(TARGET)
    assert item.title == "CRM notes" and item.source_label == "Notion" and item.external_id == TARGET
    headings = [section.heading for section in item.sections]
    assert "Commitments" in headings and "Notes" in headings
    assert fake.tools_list_calls == 1  # cached for the session


def test_older_tool_names_and_wrapped_arguments_are_mapped():
    fake = FakeNotion(tools=OLD_TOOLS)
    mcp, _f, _s, _o = _mcp(fake=fake)
    _connect(mcp, fake)
    assert mcp.account() is None  # no get-self tool: no name, no crash
    assert [hit["id"] for hit in mcp.search("crm")] == [TARGET]
    assert mcp.fetch_markdown(TARGET)[0] == "CRM notes"
    mcp.insert_after(TARGET, "- Old item", "- New item")
    assert fake.mcp_calls("page-update")[-1] == {"data": {"page_id": TARGET, "command": "insert_content_after", "selection_with_ellipsis": "- Old... item", "new_str": "- New item"}}
    mcp.replace_span(TARGET, "Phase 2 waits for KYC.", "Phase 2 starts Monday.")
    assert fake.pages[TARGET]["md"].endswith("- Old item\n- New item\n## Notes\nPhase 2 starts Monday.")


def test_session_expiry_reinitializes_once(connected):
    mcp, fake, _secrets = connected
    mcp.search("crm")
    fake.expire_next = True
    assert mcp.search("crm")[0]["id"] == TARGET
    assert len(fake.sessions) >= 1


def test_mcp_401_refreshes_once_then_retries(connected):
    mcp, fake, _secrets = connected
    fake.access.clear()  # server-side expiry before our clock says so
    assert mcp.search("crm")[0]["id"] == TARGET
    assert [f["grant_type"] for f in fake.token_forms] == ["authorization_code", "refresh_token"]


# ---- writes: create, append, add under heading, edit, guard -----------------------------


def _notes(mcp, data=None):
    from escort.context.sources.notion_source import NotionClient

    data = data if data is not None else {"notion_notes_parent": {"id": PARENT, "title": "Meetings"}}
    rest = NotionClient(MemorySecretStore(), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    return NotionNotes(rest, NotionNotesState(data), mcp=mcp), data


def test_meeting_page_is_created_under_parent_and_recap_appended(connected):
    mcp, fake, _secrets = connected
    notes, data = _notes(mcp)
    entry = notes.save_summary("mtg_1", "BayaniPay review", {"bullets": ["Phase 2 waits for KYC."], "commitments": [], "conflicts": []}, stamp="2026-11-02 10:00")
    create = fake.mcp_calls("create-pages")[0]
    assert create["parent"] == {"page_id": PARENT} and create["pages"][0]["properties"]["title"].startswith("Escort")
    page = fake.pages[entry["page_id"]]["md"]
    assert page.startswith("## Recap · 2026-11-02 10:00\n### Summary\n- Phase 2 waits for KYC.")
    notes.note_commitment("mtg_1", "BayaniPay review", "Me", "Send timeline", "Friday")
    assert fake.pages[entry["page_id"]]["md"].endswith("No conflicts against your notes.\n- Me: Send timeline (due Friday)") or "Send timeline" in fake.pages[entry["page_id"]]["md"]
    assert data["notion_notes_pages"]["mtg_1"]["page_id"] == entry["page_id"]


def test_picked_page_update_preview_then_confirm_adds_under_heading(connected):
    mcp, fake, _secrets = connected
    notes, _data = _notes(mcp)
    notes.state.add_target(TARGET, "CRM notes")
    plans = UpdatePlans(notes)
    plan = plans.commitment(TARGET, "Pia", "Send SOW", "Friday")
    assert plan["summary"] == "Add to CRM notes under Commitments:"
    assert not fake.mcp_calls("update-page")  # preview writes nothing
    plans.apply(plan)
    md = fake.pages[TARGET]["md"]
    assert "## Commitments\n- Old item\n- Pia: Send SOW" in md and md.index("Send SOW") < md.index("## Notes")


def test_edit_replaces_only_the_line_that_was_previewed(connected):
    mcp, fake, _secrets = connected
    notes, _data = _notes(mcp)
    notes.state.add_target(TARGET, "CRM notes")
    blocks = notes.read_page(TARGET)
    line = next(b for b in blocks if b["text"] == "Old item")
    assert line["type"] == "bulleted_list_item"
    plan = UpdatePlans(notes).edit(TARGET, line["id"], "Updated item")
    assert plan["lines"] == ["Before: - Old item", "After: Updated item"]
    UpdatePlans(notes).apply(plan)
    assert "- Updated item" in fake.pages[TARGET]["md"] and "Old item" not in fake.pages[TARGET]["md"]
    # The id hashes the old line: replaying the stale plan cannot touch the page again.
    with pytest.raises(Exception):
        UpdatePlans(notes).apply(plan)


def test_write_guard_refuses_pages_not_picked_before_any_tool_call(connected):
    mcp, fake, _secrets = connected
    notes, _data = _notes(mcp)
    before = len(fake.calls)
    with pytest.raises(UnknownPageError):
        UpdatePlans(notes).commitment(TARGET, "Pia", "Send SOW", None)
    with pytest.raises(UnknownPageError):
        notes.append_blocks(TARGET, [])
    assert len(fake.calls) == before


def test_duplicate_anchor_lines_grow_until_unique():
    lines, blocks = markdown_blocks("## A\n- same\n## B\n- same")
    from escort.context.sources.notion_mcp import unique_span

    assert unique_span(lines, 3) == (2, "## B\n- same")
    assert [b["type"] for b in blocks] == ["heading_2", "bulleted_list_item", "heading_2", "bulleted_list_item"]


# ---- registry: MCP preferred, REST fallback ------------------------------------------


def test_registry_prefers_mcp_and_falls_back_to_pasted_token():
    fake = FakeNotion()
    rest_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.notion.com":
            rest_seen.append(request)
            return httpx.Response(200, json={"results": [{"object": "page", "id": "rest-1", "properties": {"t": {"type": "title", "title": [{"plain_text": "From REST"}]}}}]})
        return fake(request)

    secrets = MemorySecretStore()
    secrets.set(REST_TOKEN_KEY, json.dumps({"token": REST_TOKEN, "account": "Acme"}))
    connectors = Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(handler)), open_browser=lambda _u: None)
    connectors.notion_mcp.mcp_url = MCP
    source = build_sources(connectors)["notion"]
    assert connectors.notion_auth() == "token" and [h["id"] for h in source.search("")] == ["rest-1"]
    _connect(connectors.notion_mcp, fake)
    assert connectors.notion_auth() == "oauth"
    assert [h["id"] for h in source.search("crm")] == [TARGET] and len(rest_seen) == 1
    row = connectors.status()["providers"][1]
    assert row["auth"] == "oauth" and row["account"] == "Acme HQ"
    # MCP tokens never go to the REST API.
    assert all("at-" not in r.headers.get("Authorization", "") for r in rest_seen)
