"""'Pick from Drive': API key lookup, App ID, picker-config endpoint, picked-file import."""

import io
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources import google_auth, google_drive
from escort.context.sources.base import ConnectorError, ConnectorScopeError, ConnectorSetupError
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore

CLIENT_ID = "123456789012-abcdefg.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-never-leak-this"
API_KEY = "AIzaSyTestKey_0123456789"
FILE_ID = "1PickedFileId_abcdefghijklmnopqrst"
CAL = "https://www.googleapis.com/auth/calendar.readonly"
DOCS = google_auth.DOCS_READ_SCOPE
DRIVE_FILE = google_auth.DRIVE_FILE_SCOPE
DRIVE_READONLY = "https://www.googleapis.com/auth/" + "drive" + ".readonly"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ESCORT_CONNECTORS", "ESCORT_GOOGLE_CLIENT_ID", "ESCORT_GOOGLE_CLIENT_SECRET", "ESCORT_GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _docx_bytes(text: str) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("Scope", level=1)
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class FakeDrive:
    def __init__(self, meta: dict, media: bytes = b"", exports: dict[str, httpx.Response] | None = None) -> None:
        self.meta = meta
        self.media = media
        self.exports = exports or {}
        self.requests: list[httpx.Request] = []
        self.override: httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url).startswith(google_auth.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "fresh_token", "expires_in": 3600})
        if request.url.path == "/calendar/v3/calendars/primary":
            return httpx.Response(200, json={"id": "me@example.com"})
        if self.override is not None:
            return self.override
        if request.url.path.endswith("/export"):
            return self.exports.get(request.url.params["mimeType"]) or httpx.Response(400, json={"error": {"message": "Export only supports these types"}})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=self.media)
        if request.url.path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": [self.meta]})
        if request.url.path == f"/drive/v3/files/{FILE_ID}":
            return httpx.Response(200, json=self.meta)
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _setup(scopes=(CAL, DOCS, DRIVE_READONLY), fake=None, api_key=API_KEY, expires_in=3600.0):
    fake = fake or FakeDrive({"id": FILE_ID, "name": "notes.txt", "mimeType": "text/plain", "size": "10"}, b"# Plan\nShip it")
    secrets = MemorySecretStore()
    secrets.set(google_auth.CLIENT_SECRET_KEY, CLIENT_SECRET)
    if api_key:
        secrets.set(google_auth.API_KEY_KEY, api_key)
    connectors = Connectors(
        secrets,
        client_id_getter=lambda: CLIENT_ID,
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        open_browser=lambda _url: None,
    )
    if scopes is not None:
        secrets.set(
            google_auth.TOKEN_KEY,
            json.dumps({"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + expires_in, "scope": " ".join(scopes), "account": "me@example.com"}),
        )
    return connectors, fake, secrets


def _client(tmp_path: Path, connectors, secrets) -> TestClient:
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors, monitor_lister=lambda: [])
    return TestClient(create_desktop_app(runtime))


# ---- credentials: api key + app id --------------------------------------------


def _file(tmp_path: Path, name: str, **data) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _auth(tmp_path, setup_id="", bundled=None, user=None, setup_key=""):
    secrets = MemorySecretStore()
    if setup_key:
        secrets.set(google_auth.API_KEY_KEY, setup_key)
    return Connectors(
        secrets,
        client_id_getter=lambda: setup_id,
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        bundled_path=lambda: bundled or tmp_path / "missing.json",
        user_path=lambda: user or tmp_path / "missing-user.json",
    ).google


def test_api_key_follows_the_client_id_source(tmp_path, monkeypatch):
    bundled = _file(tmp_path, "bundled.json", client_id="111-b.apps.googleusercontent.com", client_secret="s", api_key="bundled-key")
    user = _file(tmp_path, "user.json", client_id="222-u.apps.googleusercontent.com", client_secret="s", api_key="user-key")
    assert _auth(tmp_path, bundled=bundled).api_key() == "bundled-key"
    assert _auth(tmp_path, bundled=bundled, user=user).api_key() == "user-key"
    # Setup credentials never borrow a key from another project's file.
    setup = _auth(tmp_path, setup_id="333-s.apps.googleusercontent.com", bundled=bundled, user=user)
    assert setup.api_key() is None
    assert _auth(tmp_path, setup_id="333-s", bundled=bundled, setup_key=" setup-key ").api_key() == "setup-key"
    monkeypatch.setenv("ESCORT_GOOGLE_API_KEY", "env-key")
    assert setup.api_key() == "env-key"
    monkeypatch.setenv("ESCORT_GOOGLE_CLIENT_ID", "444-e.apps.googleusercontent.com")
    env = _auth(tmp_path, bundled=bundled)
    assert (env.credential_source(), env.api_key(), env.app_id()) == ("env", "env-key", "444")


def test_file_without_api_key_and_nothing_configured(tmp_path):
    bundled = _file(tmp_path, "bundled.json", client_id="111-b.apps.googleusercontent.com", client_secret="s")
    assert _auth(tmp_path, bundled=bundled).api_key() is None
    assert _auth(tmp_path).api_key() is None and _auth(tmp_path).app_id() is None


@pytest.mark.parametrize(
    "client_id, app_id",
    [
        ("123456789012-abcdefg.apps.googleusercontent.com", "123456789012"),
        ("42-x.apps.googleusercontent.com", "42"),
        ("not-a-number.apps.googleusercontent.com", None),
        ("plainid", None),
    ],
)
def test_app_id_is_the_project_number_from_the_client_id(tmp_path, client_id, app_id):
    assert _auth(tmp_path, setup_id=client_id).app_id() == app_id


def test_setup_saves_the_api_key_as_a_secret(tmp_path, monkeypatch):
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json", monitor_lister=lambda: [])
    client = TestClient(create_desktop_app(runtime))
    saved = client.post("/api/setup", json={"google_client_id": CLIENT_ID, "google_api_key": f"  {API_KEY} "})
    assert saved.status_code == 200 and API_KEY not in saved.text
    assert runtime.secrets.get("google_api_key") == API_KEY
    assert API_KEY not in (tmp_path / "config.json").read_text(encoding="utf-8")
    row = client.get("/api/connectors/status").json()["providers"][0]
    assert row["has_api_key"] is True and API_KEY not in json.dumps(row)


# ---- picker-config ------------------------------------------------------------


def test_picker_config_ready_returns_key_app_id_and_token_but_never_the_secret(tmp_path):
    connectors, _fake, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    response = client.get("/api/google/picker-config")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body == {
        "mode": "live",
        "api_key": API_KEY,
        "app_id": "123456789012",
        "access_token": "at_1",
        "scope_ok": True,
        "ready": True,
        "reason": None,
    }
    assert CLIENT_SECRET not in response.text and "rt_1" not in response.text
    # The rest of the API still never shows the key or the token.
    assert API_KEY not in client.get("/api/status").text and "at_1" not in client.get("/api/status").text


def test_picker_config_refreshes_an_expiring_token(tmp_path):
    connectors, fake, secrets = _setup(expires_in=5)
    body = _client(tmp_path, connectors, secrets).get("/api/google/picker-config").json()
    assert body["access_token"] == "fresh_token"
    assert fake.requests[0].url.path == "/token"


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"scopes": None}, "demo"),
        ({"scopes": (CAL, DOCS)}, "needs_consent"),
    ],
)
def test_picker_config_says_what_is_missing_without_a_token(tmp_path, kwargs, reason):
    connectors, _fake, secrets = _setup(**kwargs)
    body = _client(tmp_path, connectors, secrets).get("/api/google/picker-config").json()
    assert body["reason"] == reason and body["ready"] is False
    assert body["access_token"] is None and body["api_key"] is None


def test_picker_config_live_mode_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ESCORT_CONNECTORS", "live")
    connectors, _fake, secrets = _setup(scopes=None)
    assert _client(tmp_path, connectors, secrets).get("/api/google/picker-config").json()["reason"] == "not_connected"


def test_picker_config_refuses_foreign_origins(tmp_path):
    connectors, _fake, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    assert client.get("/api/google/picker-config", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/google/picker-config", headers={"Origin": "http://127.0.0.1:5173"}).status_code == 200


def test_drive_browse_consent_and_status(tmp_path):
    connectors, _fake, secrets = _setup(scopes=(CAL, DOCS))
    row = connectors.status()["providers"][0]
    assert row["capabilities"]["browse_drive"] is False and row["consent"]["drive_browse"] == "not_granted"
    writer = _setup(scopes=(CAL, DOCS, *google_auth.WRITE_SCOPES))[0]
    assert writer.status()["providers"][0]["capabilities"]["browse_drive"] is True
    assert google_auth.DRIVE_BROWSE_SCOPES == (DRIVE_READONLY,)


# ---- import-drive-file ----------------------------------------------------------


def _import(tmp_path, fake, scopes=(CAL, DOCS, DRIVE_READONLY), body=None):
    connectors, _f, secrets = _setup(scopes=scopes, fake=fake)
    client = _client(tmp_path, connectors, secrets)
    return client.post("/api/context/import-drive-file", json=body or {"id": FILE_ID, "mimeType": "application/pdf", "name": "x"})


def test_binary_docx_is_downloaded_and_extracted(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "SOW.docx", "mimeType": google_drive.DOCX_MIME, "size": "2048"}, _docx_bytes("Payment ships after KYC."))
    response = _import(tmp_path, fake)
    assert response.status_code == 200
    item = response.json()["context"]["items"][0]
    assert (item["source_label"], item["title"], item["external_id"]) == ("Google Drive", "SOW.docx", FILE_ID)
    assert any("KYC" in (s["body"] or s["quote"]) for s in item["sections"])
    media = [r for r in fake.requests if r.url.params.get("alt") == "media"]
    assert len(media) == 1 and media[0].url.path == f"/drive/v3/files/{FILE_ID}"


def test_markdown_file_without_extension_uses_the_mime_type(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "Plan", "mimeType": "text/markdown", "size": "30"}, b"# Phase 2\nCard checkout")
    item = _import(tmp_path, fake).json()["context"]["items"][0]
    assert item["title"] == "Plan" and item["sections"][0]["heading"] == "Phase 2"


def test_google_doc_is_exported_as_markdown(tmp_path):
    fake = FakeDrive(
        {"id": FILE_ID, "name": "Brief", "mimeType": google_drive.GOOGLE_DOC},
        exports={"text/markdown": httpx.Response(200, content=b"# Goals\nLaunch in May")},
    )
    item = _import(tmp_path, fake).json()["context"]["items"][0]
    assert item["sections"][0]["heading"] == "Goals"
    assert not any(r.url.params.get("alt") == "media" for r in fake.requests)


def test_google_doc_export_falls_back_to_plain_text(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "Brief", "mimeType": google_drive.GOOGLE_DOC}, exports={"text/plain": httpx.Response(200, content=b"Just text")})
    response = _import(tmp_path, fake)
    assert response.status_code == 200
    assert [r.url.params["mimeType"] for r in fake.requests if r.url.path.endswith("/export")] == ["text/markdown", "text/plain"]


def test_google_sheet_is_exported_as_csv_and_capped(tmp_path):
    rows = "\n".join(",".join(f"r{r}c{c}" for c in range(35)) for r in range(205))
    fake = FakeDrive({"id": FILE_ID, "name": "Pricing", "mimeType": google_drive.GOOGLE_SHEET}, exports={"text/csv": httpx.Response(200, content=rows.encode())})
    item = _import(tmp_path, fake).json()["context"]["items"][0]
    text = "\n".join(s["body"] or s["quote"] for s in item["sections"])
    assert "| r0c0 |" in text and "r0c30" not in text and "Showing the first 200 rows and 30 columns" in text


def test_google_slides_are_exported_as_text(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "Deck", "mimeType": google_drive.GOOGLE_SLIDES}, exports={"text/plain": httpx.Response(200, content=b"Kickoff\nPhase 2 later")})
    assert _import(tmp_path, fake).status_code == 200


def test_size_cap_is_checked_before_downloading(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "huge.pdf", "mimeType": "application/pdf", "size": str(16 * 1024 * 1024)})
    response = _import(tmp_path, fake)
    assert response.status_code == 400 and response.json()["code"] == "too_large" and "15 MB" in response.json()["error"]
    assert not any(r.url.params.get("alt") == "media" for r in fake.requests)


def test_size_cap_also_applies_to_the_downloaded_bytes():
    fake = FakeDrive({"id": FILE_ID, "name": "a.txt", "mimeType": "text/plain"}, b"x" * (google_drive.MAX_BYTES + 1))
    connectors, _f, _s = _setup(fake=fake)
    with pytest.raises(ConnectorSetupError) as caught:
        google_drive.fetch_drive_file(connectors.google, FILE_ID)
    assert caught.value.code == "too_large"


@pytest.mark.parametrize("mime, name", [("image/png", "photo.png"), ("application/zip", "bundle.zip"), ("application/vnd.google-apps.form", "Survey")])
def test_unsupported_types_are_refused_without_downloading(tmp_path, mime, name):
    fake = FakeDrive({"id": FILE_ID, "name": name, "mimeType": mime, "size": "10"})
    response = _import(tmp_path, fake)
    assert response.status_code == 400 and response.json()["code"] == "unsupported_file"
    assert not any(r.url.params.get("alt") == "media" or r.url.path.endswith("/export") for r in fake.requests)


def test_missing_drive_file_scope_prompts_without_calling_drive(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "a.txt", "mimeType": "text/plain"})
    response = _import(tmp_path, fake, scopes=(CAL, DOCS))
    body = response.json()
    assert response.status_code == 403 and body["code"] == "needs_drive_consent" and body["capability"] == "drive_browse"
    assert "Allow Drive access" in body["error"]
    assert not any("/drive/" in r.url.path for r in fake.requests)


def test_drive_errors_map_to_actionable_messages():
    fake = FakeDrive({})
    connectors, _f, _s = _setup(fake=fake)
    fake.override = httpx.Response(403, json={"error": {"message": "Insufficient Permission", "errors": [{"reason": "insufficientPermissions"}]}})
    with pytest.raises(ConnectorScopeError) as scope:
        google_drive.fetch_drive_file(connectors.google, FILE_ID)
    assert scope.value.code == "needs_drive_consent"
    fake.override = httpx.Response(
        403,
        json={"error": {"message": "Google Drive API has not been used in project 1 before or it is disabled.", "details": [{"reason": "SERVICE_DISABLED", "metadata": {"service": "drive.googleapis.com"}}]}},
    )
    with pytest.raises(ConnectorError) as disabled:
        google_drive.fetch_drive_file(connectors.google, FILE_ID)
    assert disabled.value.code == "api_disabled" and disabled.value.help_url.endswith("/drive.googleapis.com")
    fake.override = httpx.Response(404, json={"error": {"message": "File not found"}})
    with pytest.raises(ConnectorError) as missing:
        google_drive.fetch_drive_file(connectors.google, FILE_ID)
    assert missing.value.code == "not_found" and "not shared" in str(missing.value)


def test_bad_file_id_and_empty_text(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "blank.txt", "mimeType": "text/plain", "size": "3"}, b"   ")
    assert _import(tmp_path, fake, body={"id": "../../etc", "mimeType": "text/plain"}).json()["code"] == "bad_request"
    assert _import(tmp_path, fake).json()["code"] == "no_text"


def test_reimporting_the_same_file_refreshes_it(tmp_path):
    connectors, _f, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    for _ in range(2):
        response = client.post("/api/context/import-drive-file", json={"id": FILE_ID, "mimeType": "text/plain", "name": "notes.txt"})
    assert [i["title"] for i in response.json()["context"]["items"]] == ["notes.txt"]


def test_drive_search_lists_importable_files(tmp_path):
    connectors, fake, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    response = client.post("/api/context/search", json={"source": "google", "query": "notes"})
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert hits[0]["id"] == FILE_ID and hits[0]["title"] == "notes.txt"
    assert any(r.url.path == "/drive/v3/files" for r in fake.requests)


def test_drive_search_via_alias_source(tmp_path):
    connectors, _fake, secrets = _setup()
    client = _client(tmp_path, connectors, secrets)
    assert client.post("/api/context/search", json={"source": "drive", "query": ""}).status_code == 200


def test_context_import_google_uses_drive_file_fetch(tmp_path):
    fake = FakeDrive({"id": FILE_ID, "name": "notes.txt", "mimeType": "text/plain", "size": "10"}, b"# Plan\nShip it")
    connectors, _f, secrets = _setup(fake=fake)
    client = _client(tmp_path, connectors, secrets)
    item = client.post("/api/context/import", json={"source": "google", "external_id": FILE_ID}).json()["context"]["items"][0]
    assert item["source_label"] == "Google Drive"


def test_picker_config_ready_without_api_key(tmp_path):
    connectors, _fake, secrets = _setup(api_key="")
    body = _client(tmp_path, connectors, secrets).get("/api/google/picker-config").json()
    assert body["ready"] is True and body["api_key"] is None and body["access_token"] == "at_1"


def test_mock_mode_drive_import_is_labeled_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("ESCORT_CONNECTORS", "mock")
    connectors, fake, secrets = _setup(scopes=None)
    client = _client(tmp_path, connectors, secrets)
    item = client.post("/api/context/import-drive-file", json={"id": FILE_ID, "mimeType": "application/pdf", "name": "SOW.pdf"}).json()["context"]["items"][0]
    assert item["source_label"] == "Google Drive" and item["mock"] is True and "SOW.pdf" in item["title"]
    assert client.get("/api/google/picker-config").json()["reason"] == "demo"
    assert fake.requests == []
