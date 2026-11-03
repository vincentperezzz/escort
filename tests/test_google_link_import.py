"""Import from a pasted Google Docs link: parsing, documents.get conversion, errors, scope model."""

import json
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources import google_auth, google_source
from escort.context.sources.base import ConnectorError, ConnectorScopeError, ConnectorSetupError
from escort.context.sources.google_source import doc_to_markdown, parse_doc_link
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
CAL_READ = "https://www.googleapis.com/auth/calendar.readonly"
DOCS_READ = "https://www.googleapis.com/auth/documents.readonly"
DRIVE_READONLY = "https://www.googleapis.com/auth/" + "drive" + ".readonly"  # asserted absent everywhere

DOC = {
    "documentId": DOC_ID,
    "title": "BayaniPay SOW",
    "lists": {
        "ol": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}},
        "ul": {"listProperties": {"nestingLevels": [{"glyphSymbol": "●"}]}},
    },
    "body": {
        "content": [
            {"sectionBreak": {}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "TITLE"}, "elements": [{"textRun": {"content": "BayaniPay SOW\n"}}]}},
            {"paragraph": {"elements": [{"textRun": {"content": "Scope for the launch.\n"}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_2"}, "elements": [{"textRun": {"content": "Phase 2\n"}}]}},
            {"paragraph": {"elements": [{"textRun": {"content": "Payment integration "}}, {"textRun": {"content": "ships after KYC.\n"}}]}},
            {"paragraph": {"bullet": {"listId": "ul"}, "elements": [{"textRun": {"content": "Card checkout\n"}}]}},
            {"paragraph": {"bullet": {"listId": "ol"}, "elements": [{"textRun": {"content": "Sign off\n"}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_2"}, "elements": [{"textRun": {"content": "Fees\n"}}]}},
            {
                "table": {
                    "tableRows": [
                        {"tableCells": [{"content": [{"paragraph": {"elements": [{"textRun": {"content": "Item\n"}}]}}]}, {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Rate\n"}}]}}]}]},
                        {"tableCells": [{"content": [{"paragraph": {"elements": [{"textRun": {"content": "Processing\n"}}]}}]}, {"content": [{"paragraph": {"elements": [{"textRun": {"content": "2.4%\n"}}]}}]}]},
                    ]
                }
            },
        ]
    },
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ESCORT_CONNECTORS", "ESCORT_GOOGLE_CLIENT_ID", "ESCORT_GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


class FakeDocs:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.response: httpx.Response | None = None
        self.token_scope: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url).startswith(google_auth.TOKEN_URL):
            payload = {"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600}
            if self.token_scope:
                payload["scope"] = self.token_scope
            return httpx.Response(200, json=payload)
        if request.url.path == "/calendar/v3/calendars/primary":
            return httpx.Response(200, json={"id": "me@example.com"})
        if request.headers.get("Authorization") != "Bearer at_1":
            return httpx.Response(401, json={"error": {"message": "invalid"}})
        if request.url.host == "docs.googleapis.com":
            assert request.method == "GET" and request.url.path == f"/v1/documents/{DOC_ID}"
            return self.response or httpx.Response(200, json=DOC)
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _setup(scopes=(CAL_READ, DOCS_READ), fake=None):
    fake = fake or FakeDocs()
    secrets = MemorySecretStore()
    connectors = Connectors(
        secrets,
        client_id_getter=lambda: "client-123.apps.googleusercontent.com",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        open_browser=lambda _url: None,
    )
    if scopes is not None:
        secrets.set(
            google_auth.TOKEN_KEY,
            json.dumps({"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600, "scope": " ".join(scopes), "account": "me@example.com"}),
        )
    return connectors, fake, secrets


def _client(tmp_path: Path, connectors, secrets) -> TestClient:
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors, monitor_lister=lambda: [])
    return TestClient(create_desktop_app(runtime))


# ---- link parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"https://docs.google.com/document/d/{DOC_ID}/edit",
        f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing&tab=t.0#heading=h.abc",
        f"docs.google.com/document/d/{DOC_ID}",
        f"  https://docs.google.com/document/u/1/d/{DOC_ID}/edit  ",
        f"https://docs.google.com/document/d/{DOC_ID}/",
        DOC_ID,
    ],
)
def test_parse_doc_link_accepts_links_and_bare_ids(text):
    assert parse_doc_link(text) == DOC_ID


@pytest.mark.parametrize(
    "text, code",
    [
        (f"https://docs.google.com/spreadsheets/d/{DOC_ID}/edit", "wrong_kind"),
        (f"https://docs.google.com/presentation/d/{DOC_ID}/edit", "wrong_kind"),
        (f"https://docs.google.com/forms/d/{DOC_ID}/edit", "unsupported_link"),
        (f"https://drive.google.com/file/d/{DOC_ID}/view", "unsupported_link"),
        (f"https://drive.google.com/drive/folders/{DOC_ID}", "unsupported_link"),
        ("https://docs.google.com/document/d/e/2PACX-1vSomethingPublished/pub", "unsupported_link"),
        ("https://example.com/document/d/abc", "bad_link"),
        ("https://docs.google.com/document/", "bad_link"),
        ("hello world", "bad_link"),
        ("", "bad_link"),
    ],
)
def test_parse_doc_link_rejects_everything_else_clearly(text, code):
    with pytest.raises(ConnectorSetupError) as caught:
        parse_doc_link(text)
    assert caught.value.code == code
    if code == "unsupported_link" and "d/e/" not in text:
        assert "Escort reads Google Docs, Sheets and Slides links" in str(caught.value)
        assert "Import from Google" in str(caught.value)


# ---- documents.get -> sections ----------------------------------------------


def test_doc_to_markdown_handles_titles_headings_lists_and_tables():
    markdown = doc_to_markdown(DOC)
    lines = markdown.splitlines()
    assert lines[0] == "# BayaniPay SOW"
    assert "## Phase 2" in lines and "## Fees" in lines
    assert "Payment integration ships after KYC." in lines
    assert "- Card checkout" in lines and "1. Sign off" in lines
    assert "Item | Rate" in lines and "Processing | 2.4%" in lines


def test_fetch_builds_cited_sections_like_the_notion_importer():
    connectors, fake, _secrets = _setup()
    item = google_source.fetch_google_doc(connectors.google, f"https://docs.google.com/document/d/{DOC_ID}/edit")
    assert (item.source_kind, item.source_label, item.title, item.external_id, item.mock) == ("google_docs", "Google Docs", "BayaniPay SOW", DOC_ID, False)
    headings = [section.heading for section in item.sections]
    assert "Phase 2" in headings and "Fees" in headings
    phase = next(section for section in item.sections if section.heading == "Phase 2")
    assert "ships after KYC" in phase.body
    request = fake.requests[0]
    assert request.url.params["fields"] == "documentId,title,body.content,lists"


def test_import_endpoint_adds_a_google_docs_item_and_reimport_does_not_duplicate(tmp_path):
    connectors, _fake, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    link = f"https://docs.google.com/document/d/{DOC_ID}/edit"
    for _ in range(2):
        response = client.post("/api/context/import", json={"source": "google_docs", "external_id": link})
        assert response.status_code == 200
    items = response.json()["context"]["items"]
    assert [(item["source_label"], item["title"]) for item in items] == [("Google Docs", "BayaniPay SOW")]
    assert any(section["source_label"].startswith("Google Docs") for section in response.json()["meeting"]["prd_sections"])


# ---- errors -----------------------------------------------------------------


def _error(status, message, reason=None, service=None, error_status=None):
    body = {"error": {"code": status, "message": message, "status": error_status or ""}}
    if reason:
        body["error"]["details"] = [{"reason": reason, **({"metadata": {"service": service}} if service else {})}]
    return httpx.Response(status, json=body)


def test_404_means_not_found_or_not_shared():
    fake = FakeDocs()
    fake.response = _error(404, "Requested entity was not found.", error_status="NOT_FOUND")
    connectors, _fake, _s = _setup(fake=fake)
    with pytest.raises(ConnectorError) as caught:
        google_source.fetch_google_doc(connectors.google, DOC_ID)
    assert caught.value.code == "not_found" and "not shared with the Google account" in str(caught.value)


def test_403_not_shared_is_distinct_from_missing_scope_and_disabled_api():
    fake = FakeDocs()
    fake.response = _error(403, "The caller does not have permission", error_status="PERMISSION_DENIED")
    connectors, _fake, _s = _setup(fake=fake)
    with pytest.raises(ConnectorError) as caught:
        google_source.fetch_google_doc(connectors.google, DOC_ID)
    assert caught.value.code == "not_shared" and not isinstance(caught.value, ConnectorScopeError)


def test_docs_api_disabled_links_to_enable_it():
    fake = FakeDocs()
    fake.response = _error(403, "Google Docs API has not been used in project 1 before or it is disabled.", "SERVICE_DISABLED", "docs.googleapis.com", "PERMISSION_DENIED")
    connectors, _fake, _s = _setup(fake=fake)
    with pytest.raises(ConnectorError) as caught:
        google_source.fetch_google_doc(connectors.google, DOC_ID)
    assert caught.value.code == "api_disabled"
    assert caught.value.help_url == "https://console.cloud.google.com/apis/library/docs.googleapis.com"


def test_server_side_scope_error_prompts_for_docs_consent():
    fake = FakeDocs()
    fake.response = _error(403, "Request had insufficient authentication scopes.", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", error_status="PERMISSION_DENIED")
    connectors, _fake, _s = _setup(fake=fake)
    with pytest.raises(ConnectorScopeError) as caught:
        google_source.fetch_google_doc(connectors.google, DOC_ID)
    assert caught.value.code == "needs_docs_consent"


def test_expired_access_token_refreshes_and_revoked_one_disconnects():
    connectors, fake, secrets = _setup()
    secrets.set(
        google_auth.TOKEN_KEY,
        json.dumps({"access_token": "stale", "refresh_token": "rt_1", "expires_at": time.time() - 10, "scope": f"{CAL_READ} {DOCS_READ}"}),
    )
    assert google_source.fetch_google_doc(connectors.google, DOC_ID).title == "BayaniPay SOW"
    assert [r.url.path for r in fake.requests][:2] == ["/token", f"/v1/documents/{DOC_ID}"]

    def reject(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(google_auth.TOKEN_URL):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(401, json={"error": {"message": "invalid"}})

    connectors2, _f, secrets2 = _setup(fake=reject)
    with pytest.raises(ConnectorError) as caught:
        google_source.fetch_google_doc(connectors2.google, DOC_ID)
    assert caught.value.status_code == 401 and secrets2.get(google_auth.TOKEN_KEY) is None


def test_unsupported_link_over_http_is_a_400_with_the_clear_message(tmp_path):
    connectors, fake, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    response = client.post("/api/context/import", json={"source": "google_docs", "external_id": f"https://docs.google.com/forms/d/{DOC_ID}/edit"})
    assert response.status_code == 400
    assert response.json()["error"].startswith("Escort reads Google Docs, Sheets and Slides links") and response.json()["code"] == "unsupported_link"
    assert fake.requests == []


# ---- scope model and status -------------------------------------------------


def test_base_sign_in_includes_drive_readonly():
    assert set(google_auth.SCOPES) == {CAL_READ, DOCS_READ, DRIVE_READONLY}
    assert set(google_auth.WRITE_SCOPES) == {"https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/documents"}
    connectors, _fake, _s = _setup(scopes=None)
    flow = connectors.google.prepare("http://127.0.0.1:5555/", scopes=google_auth.WRITE_SCOPES)
    requested = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow.auth_url).query))["scope"].split()
    assert DRIVE_READONLY not in requested


def test_status_capabilities_for_new_and_older_tokens():
    connectors, _fake, _s = _setup()
    row = connectors.status()["providers"][0]
    assert row["capabilities"]["read_docs"] is True and row["capabilities"]["write_notes"] is False
    assert row["needs_docs_consent"] is False

    # A token from before Docs reading existed: Calendar only (plus the old Drive scope string).
    old = _setup(scopes=(CAL_READ, "https://www.googleapis.com/auth/drive" + ".readonly"))[0]
    row = old.status()["providers"][0]
    assert row["connected"] is True and row["capabilities"]["read_docs"] is False
    assert row["needs_docs_consent"] is True and "Allow Docs access" in row["setup_hint"]
    with pytest.raises(ConnectorScopeError) as caught:
        google_source.fetch_google_doc(old.google, DOC_ID)
    assert caught.value.code == "needs_docs_consent" and caught.value.status_code == 403

    # The write scope `documents` also covers reading.
    writer = _setup(scopes=(CAL_READ, *google_auth.WRITE_SCOPES))[0]
    assert writer.status()["providers"][0]["capabilities"]["read_docs"] is True


def test_reconnect_asks_for_the_base_scopes_and_hints_the_account():
    old = _setup(scopes=(CAL_READ,))[0]
    flow = old.google.prepare("http://127.0.0.1:5555/")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow.auth_url).query))
    assert set(query["scope"].split()) == {CAL_READ, DOCS_READ, DRIVE_READONLY}
    assert query["login_hint"] == "me@example.com" and query["include_granted_scopes"] == "true"


def test_declining_the_docs_box_leaves_calendar_connected_with_a_prompt():
    fake = FakeDocs()
    fake.token_scope = CAL_READ  # the user unticked the Docs box on Google's consent screen
    connectors, _fake, _s = _setup(scopes=None, fake=fake)
    flow = connectors.google.prepare("http://127.0.0.1:5555/")
    status, html = connectors.google.handle_callback({"code": "c0de", "state": flow.state})
    assert status == 200 and "Docs access was not allowed" in html
    row = connectors.status()["providers"][0]
    assert row["connected"] is True and row["needs_docs_consent"] is True


def test_mock_mode_pasted_link_gives_a_labeled_demo_doc_and_still_rejects_forms(monkeypatch, tmp_path):
    monkeypatch.setenv("ESCORT_CONNECTORS", "mock")
    connectors, fake, secrets = _setup(scopes=None)
    client = _client(tmp_path, connectors, secrets)
    ok = client.post("/api/context/import", json={"source": "google_docs", "external_id": f"https://docs.google.com/document/d/{DOC_ID}/edit"})
    assert ok.status_code == 200
    item = ok.json()["context"]["items"][0]
    assert item["source_label"] == "Google Docs" and item["mock"] is True
    bad = client.post("/api/context/import", json={"source": "google_docs", "external_id": f"https://docs.google.com/forms/d/{DOC_ID}/edit"})
    assert bad.status_code == 400
    assert fake.requests == []
