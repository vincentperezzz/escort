"""Sheets and Slides by pasted link: parsing, conversion, caps, per-capability consent, errors, mock."""

import json
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources import google_auth, google_source
from escort.context.sources.base import ConnectorError, ConnectorScopeError, ConnectorSetupError
from escort.context.sources.google_source import parse_google_link, slides_to_markdown, table_markdown
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore

FILE_ID = "1SheetOrDeckId_abcdefghijklmnopqrstuvwx"
CAL = "https://www.googleapis.com/auth/calendar.readonly"
DOCS = google_auth.DOCS_READ_SCOPE
SHEETS = google_auth.SHEETS_READ_SCOPE
SLIDES = google_auth.SLIDES_READ_SCOPE
DRIVE_READONLY = "https://www.googleapis.com/auth/" + "drive" + ".readonly"

SHEET_META = {
    "spreadsheetId": FILE_ID,
    "properties": {"title": "Pricing"},
    "sheets": [
        {"properties": {"sheetId": 0, "title": "Fees", "sheetType": "GRID"}},
        {"properties": {"sheetId": 1, "title": "Owner's notes", "sheetType": "GRID"}},
        {"properties": {"sheetId": 2, "title": "Chart1", "sheetType": "OBJECT"}},
    ],
}
SHEET_VALUES = {
    "valueRanges": [
        {"range": "Fees!A1:AE201", "values": [["Item", "Rate"], ["Processing", "2.4%"], ["Pipe | test", "multi\nline"]]},
        {"range": "'Owner''s notes'!A1:AE201"},
    ]
}

DECK = {
    "presentationId": FILE_ID,
    "title": "Kickoff",
    "slides": [
        {
            "objectId": "s1",
            "pageElements": [
                {"shape": {"placeholder": {"type": "CENTERED_TITLE"}, "text": {"textElements": [{"textRun": {"content": "BayaniPay launch\n"}}]}}},
                {"shape": {"placeholder": {"type": "SUBTITLE"}, "text": {"textElements": [{"textRun": {"content": "Phase 1 vs Phase 2\n"}}]}}},
            ],
            "slideProperties": {
                "notesPage": {
                    "notesProperties": {"speakerNotesObjectId": "n1"},
                    "pageElements": [
                        {"objectId": "n0", "shape": {"text": {"textElements": [{"textRun": {"content": "slide thumbnail text\n"}}]}}},
                        {"objectId": "n1", "shape": {"text": {"textElements": [{"textRun": {"content": "Mention KYC first.\n"}}]}}},
                    ],
                }
            },
        },
        {
            "objectId": "s2",
            "pageElements": [
                {"shape": {"placeholder": {"type": "TITLE"}, "text": {"textElements": [{"textRun": {"content": "Fees\n"}}]}}},
                {
                    "table": {
                        "tableRows": [
                            {"tableCells": [{"text": {"textElements": [{"textRun": {"content": "Item\n"}}]}}, {"text": {"textElements": [{"textRun": {"content": "Rate\n"}}]}}]},
                            {"tableCells": [{"text": {"textElements": [{"textRun": {"content": "Processing\n"}}]}}, {"text": {"textElements": [{"textRun": {"content": "2.4%\n"}}]}}]},
                        ]
                    }
                },
                {"elementGroup": {"children": [{"shape": {"text": {"textElements": [{"textRun": {"content": "# not a heading\nGrouped note\n"}}]}}}]}},
            ],
        },
        {"objectId": "s3", "pageElements": []},
    ],
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ESCORT_CONNECTORS", "ESCORT_GOOGLE_CLIENT_ID", "ESCORT_GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


class FakeGoogle:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.override: httpx.Response | None = None
        self.values = SHEET_VALUES

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/calendar/v3/calendars/primary":
            return httpx.Response(200, json={"id": "me@example.com"})
        if self.override is not None:
            return self.override
        if request.url.host == "sheets.googleapis.com":
            if request.url.path.endswith("/values:batchGet"):
                return httpx.Response(200, json=self.values)
            return httpx.Response(200, json=SHEET_META)
        if request.url.host == "slides.googleapis.com":
            return httpx.Response(200, json=DECK)
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _setup(scopes=(CAL, DOCS, SHEETS, SLIDES), fake=None):
    fake = fake or FakeGoogle()
    secrets = MemorySecretStore()
    connectors = Connectors(
        secrets,
        client_id_getter=lambda: "123456789012-abc.apps.googleusercontent.com",
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


def _error(status, message, reason=None, service=None, error_status=""):
    body = {"error": {"code": status, "message": message, "status": error_status}}
    if reason:
        body["error"]["details"] = [{"reason": reason, **({"metadata": {"service": service}} if service else {})}]
    return httpx.Response(status, json=body)


# ---- link parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    "text, kind",
    [
        (f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit#gid=0", "sheets"),
        (f"docs.google.com/spreadsheets/u/0/d/{FILE_ID}", "sheets"),
        (f"https://docs.google.com/presentation/d/{FILE_ID}/edit#slide=id.p", "slides"),
        (f"https://docs.google.com/document/d/{FILE_ID}/edit", "docs"),
    ],
)
def test_links_are_detected_by_type(text, kind):
    assert parse_google_link(text) == (kind, FILE_ID)


def test_bare_id_takes_the_selected_type():
    assert parse_google_link(FILE_ID) == ("docs", FILE_ID)
    assert parse_google_link(FILE_ID, "sheets") == ("sheets", FILE_ID)
    assert parse_google_link(FILE_ID, "slides") == ("slides", FILE_ID)
    # A link wins over the selected type.
    assert parse_google_link(f"https://docs.google.com/presentation/d/{FILE_ID}/edit", "sheets") == ("slides", FILE_ID)


@pytest.mark.parametrize(
    "text",
    [
        "https://docs.google.com/spreadsheets/d/e/2PACX-published/pubhtml",
        "https://docs.google.com/presentation/d/e/2PACX-published/pub",
        f"https://drive.google.com/file/d/{FILE_ID}/view",
    ],
)
def test_published_and_drive_links_are_refused(text):
    with pytest.raises(ConnectorSetupError) as caught:
        parse_google_link(text)
    assert caught.value.code == "unsupported_link"


# ---- Sheets -----------------------------------------------------------------


def test_sheet_becomes_one_markdown_table_per_tab():
    connectors, fake, _s = _setup()
    item = google_source.fetch_google_sheet(connectors.google, FILE_ID)
    assert (item.source_kind, item.source_label, item.title, item.external_id) == ("google_sheets", "Google Sheets", "Pricing", FILE_ID)
    by_heading = {section.heading: section.body for section in item.sections}
    assert "| Item | Rate |" in by_heading["Fees"]
    assert "| Processing | 2.4% |" in by_heading["Fees"]
    assert "| Pipe \\| test | multi line |" in by_heading["Fees"]
    assert "empty" in by_heading["Owner's notes"]
    assert "Chart1" not in by_heading  # chart-only tabs have no cells

    meta, batch = fake.requests
    assert meta.url.params["includeGridData"] == "false"
    assert batch.url.path == f"/v4/spreadsheets/{FILE_ID}/values:batchGet"
    # One extra row and column per tab detects truncation exactly; quotes in tab names are doubled.
    assert batch.url.params.get_list("ranges") == ["'Fees'!A1:AE201", "'Owner''s notes'!A1:AE201"]


def test_sheet_caps_rows_and_columns_and_says_so():
    rows = [[f"r{r}c{c}" for c in range(40)] for r in range(250)]
    lines, truncated = table_markdown(rows)
    assert truncated is True
    assert len(lines) == 2 + 199  # header + divider + 199 data rows = 200 sheet rows
    assert lines[0].count("|") == 31  # 30 columns
    assert "r0c29" in lines[0] and "r0c30" not in lines[0]

    fake = FakeGoogle()
    fake.values = {"valueRanges": [{"values": rows}, {"values": [["a"]]}]}
    connectors, _f, _s = _setup(fake=fake)
    fees = next(s for s in google_source.fetch_google_sheet(connectors.google, FILE_ID).sections if s.heading == "Fees")
    assert "Showing the first 200 rows and 30 columns" in fees.body

    small, cut = table_markdown([["a", "b"], ["c"]])
    assert cut is False and small == ["| a | b |", "| --- | --- |", "| c |  |"]


def test_sheet_tab_limit():
    many = {**SHEET_META, "sheets": [{"properties": {"title": f"T{i}", "sheetType": "GRID"}} for i in range(25)]}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(":batchGet"):
            return httpx.Response(200, json={"valueRanges": [{"values": [["x"]]}] * 20})
        return httpx.Response(200, json=many)

    connectors, _f, _s = _setup(fake=handler)
    item = google_source.fetch_google_sheet(connectors.google, FILE_ID)
    assert len(seen[-1].url.params.get_list("ranges")) == 20
    assert "5 more tabs not imported" in "\n".join(s.body for s in item.sections)


# ---- Slides -----------------------------------------------------------------


def test_slides_become_one_section_per_slide_with_notes_and_tables():
    markdown = slides_to_markdown(DECK)
    assert "## Slide 1: BayaniPay launch" in markdown and "## Slide 2: Fees" in markdown and "## Slide 3" in markdown
    connectors, fake, _s = _setup()
    item = google_source.fetch_google_slides(connectors.google, FILE_ID)
    assert (item.source_kind, item.source_label, item.title) == ("google_slides", "Google Slides", "Kickoff")
    by_heading = {section.heading: section.body for section in item.sections}
    assert "Phase 1 vs Phase 2" in by_heading["Slide 1: BayaniPay launch"]
    assert "Speaker notes:\nMention KYC first." in by_heading["Slide 1: BayaniPay launch"]
    assert "thumbnail" not in by_heading["Slide 1: BayaniPay launch"]
    assert "Item | Rate" in by_heading["Slide 2: Fees"] and "Processing | 2.4%" in by_heading["Slide 2: Fees"]
    assert "\\# not a heading" in by_heading["Slide 2: Fees"] and "Grouped note" in by_heading["Slide 2: Fees"]
    assert "No text on this slide" in by_heading["Slide 3"]
    assert fake.requests[0].url.path == f"/v1/presentations/{FILE_ID}"


# ---- per-capability consent ---------------------------------------------------


@pytest.mark.parametrize(
    "kind, link, code, button",
    [
        ("sheets", f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit", "needs_sheets_consent", "Allow Sheets access"),
        ("slides", f"https://docs.google.com/presentation/d/{FILE_ID}/edit", "needs_slides_consent", "Allow Slides access"),
    ],
)
def test_missing_scope_prompts_for_that_capability_without_calling_google(tmp_path, kind, link, code, button):
    connectors, fake, secrets = _setup(scopes=(CAL, DOCS))
    client = _client(tmp_path, connectors, secrets)
    response = client.post("/api/context/import", json={"source": "google_docs", "external_id": link})
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == code and body["capability"] == kind and button in body["error"]
    assert [r for r in fake.requests if r.url.host.endswith("googleapis.com") and "calendar" not in r.url.path] == []


def test_server_side_scope_error_maps_by_api_host():
    fake = FakeGoogle()
    fake.override = _error(403, "Request had insufficient authentication scopes.", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", error_status="PERMISSION_DENIED")
    connectors, _f, _s = _setup(fake=fake)
    with pytest.raises(ConnectorScopeError) as sheets:
        google_source.fetch_google_sheet(connectors.google, FILE_ID)
    with pytest.raises(ConnectorScopeError) as slides:
        google_source.fetch_google_slides(connectors.google, FILE_ID)
    assert (sheets.value.code, slides.value.code) == ("needs_sheets_consent", "needs_slides_consent")


def test_consent_endpoint_asks_only_for_the_one_scope(tmp_path):
    opened: list[str] = []
    connectors, _f, secrets = _setup(scopes=(CAL, DOCS))
    connectors.google._open_browser = opened.append
    client = _client(tmp_path, connectors, secrets)
    for capability, scope in (("sheets", SHEETS), ("slides", SLIDES), ("drive_files", google_auth.DRIVE_FILE_SCOPE)):
        out = client.post("/api/connectors/google/consent", json={"capability": capability}).json()
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(out["auth_url"]).query))
        assert query["scope"].split() == [scope] and query["include_granted_scopes"] == "true"
        assert DRIVE_READONLY not in query["scope"]
        row = client.get("/api/connectors/status").json()["providers"][0]
        assert row["consent"][capability] == "pending"
    connectors.google.cancel()
    assert client.post("/api/connectors/google/consent", json={"capability": "everything"}).status_code == 400


def test_callback_reports_a_declined_capability():
    fake = FakeGoogle()
    connectors, _f, _s = _setup(scopes=(CAL, DOCS), fake=fake)

    def token(request):
        if str(request.url).startswith(google_auth.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at_2", "expires_in": 3600, "scope": f"{CAL} {DOCS}"})
        return fake(request)

    connectors.google.http = httpx.Client(transport=httpx.MockTransport(token))
    flow = connectors.google.prepare("http://127.0.0.1:5555/", scopes=google_auth.SHEETS_SCOPES)
    assert flow.capability == "sheets"
    status, html = connectors.google.handle_callback({"code": "c", "state": flow.state})
    assert status == 200 and "not allowed" in html
    assert connectors.google.can("sheets") is False and "read Google Sheets" in connectors.google.last_error


def test_capabilities_and_base_scopes():
    assert set(google_auth.SCOPES) == {CAL, DOCS}  # Sheets/Slides never join the base sign-in
    connectors, _f, _s = _setup(scopes=(CAL, DOCS, SHEETS))
    row = connectors.status()["providers"][0]
    assert row["capabilities"]["read_sheets"] is True and row["capabilities"]["read_slides"] is False
    assert row["consent"]["sheets"] == "granted" and row["consent"]["slides"] == "not_granted"
    assert row["needs_docs_consent"] is False
    for scopes in (google_auth.SCOPES, google_auth.SHEETS_SCOPES, google_auth.SLIDES_SCOPES, google_auth.PICKER_SCOPES, google_auth.WRITE_SCOPES):
        assert DRIVE_READONLY not in scopes


# ---- errors -----------------------------------------------------------------


def test_not_shared_and_not_found_wording():
    fake = FakeGoogle()
    fake.override = _error(403, "The caller does not have permission", error_status="PERMISSION_DENIED")
    connectors, _f, _s = _setup(fake=fake)
    with pytest.raises(ConnectorError) as caught:
        google_source.fetch_google_sheet(connectors.google, FILE_ID)
    assert caught.value.code == "not_shared" and "spreadsheet is not shared" in str(caught.value)
    fake.override = _error(404, "Requested entity was not found.", error_status="NOT_FOUND")
    with pytest.raises(ConnectorError) as missing:
        google_source.fetch_google_slides(connectors.google, FILE_ID)
    assert missing.value.code == "not_found" and "presentation" in str(missing.value)


@pytest.mark.parametrize("host, fetch", [("sheets.googleapis.com", "fetch_google_sheet"), ("slides.googleapis.com", "fetch_google_slides")])
def test_disabled_api_links_to_enable_it(host, fetch):
    fake = FakeGoogle()
    fake.override = _error(403, "API has not been used in project 1 before or it is disabled.", "SERVICE_DISABLED", host, "PERMISSION_DENIED")
    connectors, _f, _s = _setup(fake=fake)
    with pytest.raises(ConnectorError) as caught:
        getattr(google_source, fetch)(connectors.google, FILE_ID)
    assert caught.value.code == "api_disabled"
    assert caught.value.help_url == f"https://console.cloud.google.com/apis/library/{host}"


# ---- HTTP + mock ------------------------------------------------------------


def test_import_endpoint_for_sheets_and_bare_slides_id(tmp_path):
    connectors, _f, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    for _ in range(2):
        sheet = client.post("/api/context/import", json={"source": "google_docs", "external_id": f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit"})
        assert sheet.status_code == 200
    deck = client.post("/api/context/import", json={"source": "google_slides", "external_id": FILE_ID})
    assert deck.status_code == 200
    items = deck.json()["context"]["items"]
    assert [(item["source_label"], item["title"]) for item in items] == [("Google Sheets", "Pricing"), ("Google Slides", "Kickoff")]


def test_mock_mode_returns_labeled_demo_sheets_and_slides(monkeypatch, tmp_path):
    monkeypatch.setenv("ESCORT_CONNECTORS", "mock")
    connectors, fake, secrets = _setup(scopes=None)
    client = _client(tmp_path, connectors, secrets)
    sheet = client.post("/api/context/import", json={"source": "google_docs", "external_id": f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit"})
    deck = client.post("/api/context/import", json={"source": "google_slides", "external_id": FILE_ID})
    assert sheet.status_code == deck.status_code == 200
    items = deck.json()["context"]["items"]
    assert [(i["source_label"], i["mock"]) for i in items] == [("Google Sheets", True), ("Google Slides", True)]
    assert fake.requests == []
