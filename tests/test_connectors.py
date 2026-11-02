import base64
import hashlib
import json
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.library import ContextLibrary
from escort.context.sources import google_auth
from escort.context.sources.base import ConnectorAuthError, ConnectorSetupError
from escort.context.sources.registry import Connectors, build_sources, use_mock
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore

CALENDAR_ITEMS = {
    "items": [
        {
            "id": "evt_real",
            "summary": "Client kickoff",
            "start": {"dateTime": "2026-11-03T09:00:00+08:00"},
            "end": {"dateTime": "2026-11-03T10:00:00+08:00"},
            "description": "Scope walk-through",
            "attendees": [{"email": "client@example.com"}],
            "attachments": [{"title": "Kickoff SOW"}],
        },
        {"id": "evt_gone", "status": "cancelled", "summary": "Cancelled", "start": {}, "end": {}},
    ]
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ESCORT_CONNECTORS", "ESCORT_GOOGLE_CLIENT_ID", "ESCORT_GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


class FakeGoogle:
    """Records requests and plays Google's token/API endpoints."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.valid_access = {"at_1"}
        self.refresh_ok = True
        self.issued = 1

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(google_auth.TOKEN_URL):
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            if form["grant_type"] == "authorization_code":
                return httpx.Response(200, json={"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600})
            if form["grant_type"] == "refresh_token":
                if not self.refresh_ok:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.issued += 1
                token = f"at_{self.issued}"
                self.valid_access.add(token)
                return httpx.Response(200, json={"access_token": token, "expires_in": 3600})
        if url.startswith(google_auth.REVOKE_URL):
            return httpx.Response(200)
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if bearer not in self.valid_access:
            return httpx.Response(401, json={"error": {"message": "invalid credentials"}})
        if request.url.path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "me@example.com"}})
        if request.url.path == "/calendar/v3/calendars/primary/events":
            return httpx.Response(200, json=CALENDAR_ITEMS)
        if request.url.path == "/drive/v3/files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"id": "doc1", "name": "Kickoff SOW", "mimeType": "application/vnd.google-apps.document", "modifiedTime": "2026-10-30T01:00:00Z"},
                        {"id": "dir1", "name": "Folder", "mimeType": "application/vnd.google-apps.folder"},
                    ]
                },
            )
        if request.url.path == "/drive/v3/files/doc1":
            return httpx.Response(200, json={"id": "doc1", "name": "Kickoff SOW", "mimeType": "application/vnd.google-apps.document"})
        if request.url.path == "/drive/v3/files/doc1/export":
            assert request.url.params["mimeType"] == "text/markdown"
            return httpx.Response(200, text="Intro line.\n\n## 2.1 Payments\nPayments are Phase 2.\n")
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _google(secrets=None, fake=None, opened=None) -> tuple[Connectors, FakeGoogle, MemorySecretStore]:
    secrets = secrets or MemorySecretStore()
    fake = fake or FakeGoogle()
    http = httpx.Client(transport=httpx.MockTransport(fake))
    connectors = Connectors(
        secrets,
        client_id_getter=lambda: "client-123.apps.googleusercontent.com",
        http=http,
        open_browser=(opened.append if opened is not None else (lambda _url: None)),
    )
    return connectors, fake, secrets


def _store_tokens(secrets, access="at_1", refresh="rt_1", expires_at=None):
    secrets.set(
        google_auth.TOKEN_KEY,
        json.dumps({"access_token": access, "refresh_token": refresh, "expires_at": expires_at or time.time() + 3600, "account": "me@example.com"}),
    )


# ---- mode selection ---------------------------------------------------------


def test_use_mock_modes(monkeypatch):
    assert use_mock(connected=False) is True
    assert use_mock(connected=True) is False
    monkeypatch.setenv("ESCORT_CONNECTORS", "live")
    assert use_mock(connected=False) is False
    monkeypatch.setenv("ESCORT_CONNECTORS", "mock")
    assert use_mock(connected=True) is True
    monkeypatch.setenv("ESCORT_CONNECTORS", "bogus")
    assert use_mock(connected=True) is False


def test_auto_mode_mock_until_connected_then_never_mock():
    connectors, _fake, secrets = _google()
    sources = build_sources(connectors)
    events = sources["google_calendar"].list_upcoming_events(5)
    assert events and all(event.mock for event in events)
    hits = sources["drive"].search("")
    assert hits and all(hit["mock"] for hit in hits)

    _store_tokens(secrets)
    events = sources["google_calendar"].list_upcoming_events(5)
    assert [event.id for event in events] == ["evt_real"]
    assert not any(event.mock for event in events)
    assert all(not hit["mock"] for hit in sources["drive"].search(""))


def test_live_mode_without_connection_returns_nothing(monkeypatch):
    monkeypatch.setenv("ESCORT_CONNECTORS", "live")
    connectors, _fake, _secrets = _google()
    sources = build_sources(connectors)
    assert sources["google_calendar"].list_upcoming_events(5) == []
    assert sources["notion"].search("x") == []
    with pytest.raises(ConnectorAuthError):
        sources["drive"].fetch("doc1")


def test_explicit_mock_mode_overrides_connection(monkeypatch):
    monkeypatch.setenv("ESCORT_CONNECTORS", "mock")
    connectors, fake, secrets = _google()
    _store_tokens(secrets)
    events = build_sources(connectors)["google_calendar"].list_upcoming_events(5)
    assert all(event.mock for event in events)
    assert fake.requests == []


# ---- Google OAuth -----------------------------------------------------------


def test_auth_url_uses_pkce_s256_and_state():
    connectors, _fake, _secrets = _google()
    flow = connectors.google.prepare("http://127.0.0.1:5555/")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow.auth_url).query))
    assert query["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == expected
    assert query["state"] == flow.state
    assert query["redirect_uri"] == "http://127.0.0.1:5555/"
    assert query["access_type"] == "offline"
    assert set(query["scope"].split()) == set(google_auth.SCOPES)
    assert 43 <= len(flow.verifier) <= 128


def test_state_mismatch_is_rejected_without_token_exchange():
    connectors, fake, secrets = _google()
    flow = connectors.google.prepare("http://127.0.0.1:5555/")
    status, html = connectors.google.handle_callback({"code": "c0de", "state": "forged"})
    assert status == 400
    assert "state mismatch" in html
    assert fake.requests == []
    assert secrets.get(google_auth.TOKEN_KEY) is None
    # The genuine redirect still completes afterwards.
    status, _html = connectors.google.handle_callback({"code": "c0de", "state": flow.state})
    assert status == 200
    assert connectors.google.connected()


def test_missing_state_and_no_pending_flow_are_rejected():
    connectors, fake, _secrets = _google()
    assert connectors.google.handle_callback({"code": "c0de", "state": "x"})[0] == 400
    connectors.google.prepare("http://127.0.0.1:5555/")
    assert connectors.google.handle_callback({"code": "c0de"})[0] == 400
    assert fake.requests == []


def test_code_exchange_sends_verifier_and_stores_tokens(monkeypatch):
    monkeypatch.setenv("ESCORT_GOOGLE_CLIENT_SECRET", "desktop-secret")
    connectors, fake, secrets = _google()
    flow = connectors.google.prepare("http://127.0.0.1:5555/")
    status, html = connectors.google.handle_callback({"code": "c0de", "state": flow.state})
    assert status == 200 and "close this tab" in html
    form = dict(urllib.parse.parse_qsl(fake.requests[0].content.decode()))
    assert form == {
        "code": "c0de",
        "client_id": "client-123.apps.googleusercontent.com",
        "client_secret": "desktop-secret",
        "code_verifier": flow.verifier,
        "grant_type": "authorization_code",
        "redirect_uri": "http://127.0.0.1:5555/",
    }
    stored = json.loads(secrets.get(google_auth.TOKEN_KEY))
    assert stored["access_token"] == "at_1" and stored["refresh_token"] == "rt_1"
    assert stored["expires_at"] > time.time() + 3000
    assert connectors.google.account() == "me@example.com"
    # A replayed redirect after completion is refused.
    assert connectors.google.handle_callback({"code": "c0de", "state": flow.state})[0] == 400


def test_user_denied_consent_reports_error():
    connectors, fake, _secrets = _google()
    flow = connectors.google.prepare("http://127.0.0.1:5555/")
    status, _ = connectors.google.handle_callback({"error": "access_denied", "state": flow.state})
    assert status == 400
    assert not connectors.google.connected()
    assert "access_denied" in connectors.status()["providers"][0]["setup_hint"]
    assert fake.requests == []


def test_loopback_server_completes_real_redirect():
    opened: list[str] = []
    connectors, _fake, _secrets = _google(opened=opened)
    auth_url = connectors.connect_google()["auth_url"]
    assert opened == [auth_url]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(auth_url).query))
    redirect = query["redirect_uri"]
    assert redirect.startswith("http://127.0.0.1:") and not redirect.startswith("http://127.0.0.1:0/")
    with httpx.Client(trust_env=False, timeout=10) as real:
        assert real.get(redirect + "favicon.ico").status_code == 404
        bad = real.get(redirect, params={"code": "c0de", "state": "nope"})
        assert bad.status_code == 400
        good = real.get(redirect, params={"code": "c0de", "state": query["state"]})
    assert good.status_code == 200
    assert "close this tab" in good.text
    assert connectors.google.connected()


def test_connect_requires_client_id():
    secrets = MemorySecretStore()
    connectors = Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(FakeGoogle())), bundled_path=lambda: Path("missing-google-client.json"))
    row = connectors.status()["providers"][0]
    assert row["configured"] is False and "Google app credentials" in row["setup_hint"]
    with pytest.raises(ConnectorSetupError):
        connectors.connect_google()


def test_expired_access_token_is_refreshed():
    connectors, fake, secrets = _google()
    _store_tokens(secrets, access="stale", expires_at=time.time() - 10)
    events = build_sources(connectors)["google_calendar"].list_upcoming_events(5)
    assert events[0].title == "Client kickoff"
    refresh = dict(urllib.parse.parse_qsl(fake.requests[0].content.decode()))
    assert refresh["grant_type"] == "refresh_token" and refresh["refresh_token"] == "rt_1"
    stored = json.loads(secrets.get(google_auth.TOKEN_KEY))
    assert stored["access_token"] == "at_2" and stored["refresh_token"] == "rt_1"


def test_401_triggers_one_refresh_and_retry():
    connectors, fake, secrets = _google()
    _store_tokens(secrets, access="revoked_access")  # not expired, but server rejects it
    hits = build_sources(connectors)["drive"].search("kickoff")
    assert [hit["id"] for hit in hits] == ["doc1"]
    paths = [request.url.path for request in fake.requests]
    assert paths == ["/drive/v3/files", "/token", "/drive/v3/files"]


def test_revoked_refresh_token_disconnects_and_never_falls_back_to_mock():
    fake = FakeGoogle()
    fake.refresh_ok = False
    connectors, _fake, secrets = _google(fake=fake)
    _store_tokens(secrets, access="stale", expires_at=time.time() - 10)
    source = build_sources(connectors)["drive"]
    with pytest.raises(ConnectorAuthError):
        source.search("kickoff")
    assert secrets.get(google_auth.TOKEN_KEY) is None
    row = connectors.status()["providers"][0]
    assert row["connected"] is False and row["account"] is None
    assert "revoked" in row["setup_hint"]


def test_disconnect_revokes_and_clears():
    connectors, fake, secrets = _google()
    _store_tokens(secrets)
    connectors.disconnect("google")
    assert secrets.get(google_auth.TOKEN_KEY) is None
    revoke = fake.requests[-1]
    assert str(revoke.url).startswith(google_auth.REVOKE_URL)
    assert dict(urllib.parse.parse_qsl(revoke.content.decode()))["token"] == "rt_1"


# ---- Calendar / Drive -------------------------------------------------------


def test_calendar_maps_events_and_caches():
    connectors, fake, secrets = _google()
    _store_tokens(secrets)
    calendar = build_sources(connectors)["google_calendar"]
    events = calendar.list_upcoming_events(5)
    assert len(events) == 1
    event = events[0]
    assert event.start == "2026-11-03T09:00:00+08:00"
    assert event.attendees == ["client@example.com"]
    assert event.linked_doc_hints == ["Kickoff SOW"]
    params = fake.requests[0].url.params
    assert params["singleEvents"] == "true" and params["orderBy"] == "startTime" and params["timeMin"].endswith("Z")
    calendar.list_upcoming_events(3)
    assert len(fake.requests) == 1


def test_drive_search_escapes_query_and_fetch_exports_doc():
    connectors, fake, secrets = _google()
    _store_tokens(secrets)
    drive = build_sources(connectors)["drive"]
    hits = drive.search("Bob's \\ plan")
    assert [hit["id"] for hit in hits] == ["doc1"]
    q = fake.requests[0].url.params["q"]
    assert "name contains 'Bob\\'s \\\\ plan'" in q and "trashed = false" in q
    item = drive.fetch("doc1")
    assert item.source_kind == "drive" and item.title == "Kickoff SOW" and item.mock is False
    headings = [section.heading for section in item.sections]
    assert headings == ["Kickoff SOW", "2.1 Payments"]
    assert item.sections[0].body == "Intro line."


# ---- Notion -----------------------------------------------------------------


def _notion_handler(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("Authorization") != "Bearer secret_good":
            return httpx.Response(401, json={"message": "API token is invalid."})
        assert request.headers["Notion-Version"]
        path = request.url.path
        if path == "/v1/users/me":
            return httpx.Response(200, json={"object": "user", "type": "bot", "name": "Escort", "bot": {"workspace_name": "Acme"}})
        if path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "url": "https://notion.so/page-1",
                            "last_edited_time": "2026-10-31T00:00:00.000Z",
                            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Phase scope"}]}},
                        }
                    ]
                },
            )
        if path == "/v1/pages/page-1":
            return httpx.Response(200, json={"object": "page", "id": "page-1", "properties": {"title": {"type": "title", "title": [{"plain_text": "Phase scope"}]}}})
        if path == "/v1/blocks/page-1/children":
            if request.url.params.get("start_cursor") == "c2":
                return httpx.Response(200, json={"results": [{"id": "b3", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "KYC in Phase 1"}]}}], "has_more": False})
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "b1", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Overview text"}]}},
                        {"id": "b2", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "2.2 Phase 2"}]}, "has_children": False},
                        {"id": "tog", "type": "toggle", "toggle": {"rich_text": [{"plain_text": "Payments"}]}, "has_children": True},
                    ],
                    "has_more": True,
                    "next_cursor": "c2",
                },
            )
        if path == "/v1/blocks/tog/children":
            return httpx.Response(200, json={"results": [{"id": "n1", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Card checkout later"}]}}], "has_more": False})
        return httpx.Response(404, json={"message": "nope"})

    return handler


def _notion() -> tuple[Connectors, list[httpx.Request], MemorySecretStore]:
    seen: list[httpx.Request] = []
    secrets = MemorySecretStore()
    return Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(_notion_handler(seen)))), seen, secrets


def test_notion_connect_validates_token_before_saving():
    connectors, seen, secrets = _notion()
    with pytest.raises(ConnectorSetupError):
        connectors.connect_notion("secret_bad")
    assert secrets.get("notion_oauth_token") is None
    status = connectors.connect_notion("secret_good")
    row = status["providers"][1]
    assert row == {**row, "id": "notion", "connected": True, "account": "Acme", "mode": "live", "configured": True}
    assert seen[-1].url.path == "/v1/users/me"


def test_notion_search_and_fetch_blocks_to_sections():
    connectors, seen, _secrets = _notion()
    connectors.connect_notion("secret_good")
    source = build_sources(connectors)["notion"]
    hits = source.search("phase")
    assert hits == [{"id": "page-1", "title": "Phase scope", "snippet": "Notion page · edited 2026-10-31", "url": "https://notion.so/page-1", "mock": False}]
    body = json.loads(seen[-1].content)
    assert body["query"] == "phase" and body["filter"] == {"property": "object", "value": "page"}
    item = source.fetch("page-1")
    assert item.title == "Phase scope" and item.source_label == "Notion"
    by_heading = {section.heading: section.body for section in item.sections}
    assert by_heading["Phase scope"] == "Overview text"
    assert "Card checkout later" in by_heading["2.2 Phase 2"]
    assert "- KYC in Phase 1" in by_heading["2.2 Phase 2"]


def test_notion_revoked_token_reports_disconnected():
    connectors, _seen, secrets = _notion()
    secrets.set("notion_oauth_token", json.dumps({"token": "secret_revoked", "account": "Acme"}))
    with pytest.raises(ConnectorAuthError):
        build_sources(connectors)["notion"].search("x")
    assert connectors.notion.connected() is False


# ---- HTTP API contract ------------------------------------------------------


def _runtime(tmp_path: Path, connectors: Connectors, secrets: MemorySecretStore) -> TestClient:
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors)
    return TestClient(create_desktop_app(runtime))


def test_status_contract_and_no_token_leak(tmp_path: Path):
    connectors, _fake, secrets = _google()
    _store_tokens(secrets, access="at_1", refresh="rt_secret_value")
    client = _runtime(tmp_path, connectors, secrets)
    status = client.get("/api/connectors/status").json()
    assert [row["id"] for row in status["providers"]] == ["google", "notion"]
    for row in status["providers"]:
        assert {"id", "label", "connected", "account", "mode", "configured", "setup_hint"} <= set(row)
        assert set(row) - {"id", "label", "connected", "account", "mode", "configured", "setup_hint"} <= {"credentials_source", "has_client_secret"}
    google = status["providers"][0]
    assert google["connected"] is True and google["account"] == "me@example.com" and google["mode"] == "live" and google["configured"] is True
    notion = status["providers"][1]
    assert notion["connected"] is False and notion["account"] is None and notion["mode"] == "mock"
    full = client.get("/api/status")
    assert full.json()["connectors"] == status
    assert "rt_secret_value" not in full.text and "at_1" not in full.text


def test_api_connect_disconnect_flow(tmp_path: Path):
    opened: list[str] = []
    connectors, _fake, secrets = _google(opened=opened)
    client = _runtime(tmp_path, connectors, secrets)
    started = client.post("/api/connectors/google/connect")
    assert started.status_code == 200
    assert started.json()["auth_url"].startswith(google_auth.AUTH_URL)
    assert opened == [started.json()["auth_url"]]
    assert "Finish signing in" in client.get("/api/connectors/status").json()["providers"][0]["setup_hint"]
    _store_tokens(secrets)
    out = client.post("/api/connectors/google/disconnect")
    assert out.status_code == 200 and out.json()["providers"][0]["connected"] is False
    assert client.post("/api/connectors/slack/disconnect").status_code == 404


def test_api_google_connect_unconfigured_is_400(tmp_path: Path):
    secrets = MemorySecretStore()
    client = _runtime(tmp_path, Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(FakeGoogle()))), secrets)
    response = client.post("/api/connectors/google/connect")
    assert response.status_code == 400
    assert response.json()["provider"] == "google"


def test_api_notion_connect_and_compat_token(tmp_path: Path):
    connectors, _seen, secrets = _notion()
    client = _runtime(tmp_path, connectors, secrets)
    bad = client.post("/api/connectors/notion/connect", json={"token": "secret_bad"})
    assert bad.status_code == 400 and "rejected" in bad.json()["error"]
    good = client.post("/api/connectors/notion/connect", json={"token": "secret_good"})
    assert good.status_code == 200 and good.json()["providers"][1]["account"] == "Acme"
    hits = client.post("/api/context/search", json={"source": "notion", "query": "phase"}).json()["hits"]
    assert hits[0]["id"] == "page-1" and hits[0]["mock"] is False
    imported = client.post("/api/context/import", json={"source": "notion", "external_id": "page-1"})
    assert imported.json()["context"]["items"][0]["title"] == "Phase scope"
    client.post("/api/connectors/notion/disconnect")
    compat = client.post("/api/connectors/token", json={"name": "notion", "token": "secret_good"})
    assert compat.json()["connectors"]["providers"][1]["connected"] is True
    assert "secret_good" not in compat.text


def test_api_connector_errors_are_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ESCORT_CONNECTORS", "live")
    connectors, _seen, secrets = _notion()
    client = _runtime(tmp_path, connectors, secrets)
    response = client.post("/api/context/import", json={"source": "notion", "external_id": "page-1"})
    assert response.status_code == 401 and response.json()["provider"] == "notion"
    assert client.post("/api/context/search", json={"source": "nope", "query": ""}).status_code == 400


def test_library_getter_only_still_uses_mock_in_auto(tmp_path: Path):
    lib = ContextLibrary(tmp_path, lambda _key: None)
    view = lib.public_view()
    assert view["upcoming"] and all(event["mock"] for event in view["upcoming"])
    assert {row["mode"] for row in view["connectors"]} == {"mock"}
