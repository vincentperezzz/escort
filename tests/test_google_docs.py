"""Save notes to Google Docs: incremental consent, the doc registry, and user-clicked writes (no network)."""

import json
import logging
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources import google_auth
from escort.context.sources.base import ConnectorScopeError
from escort.context.sources.google_auth import SCOPES, WRITE_SCOPES
from escort.context.sources.google_docs import (
    DOC_MIME,
    FOLDER_MIME,
    DictNotesState,
    GoogleNotes,
    UnknownDocError,
    doc_title,
    structured_requests,
    summary_blocks,
)
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.models import CardRecord
from escort.store import MeetingStore

CLIENT_SECRET = "GOCSPX-desktop-secret-value"
FULL_DRIVE = "https://www.googleapis.com/auth/drive"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ESCORT_CONNECTORS", "ESCORT_GOOGLE_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ESCORT_GOOGLE_CLIENT_SECRET", CLIENT_SECRET)


class FakeWorkspace:
    """Google token, Drive and Docs endpoints, with scope checks like the real ones."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.folders: dict[str, dict] = {}
        self.docs: dict[str, int] = {}  # doc id -> body end index
        self.listable_folders: list[str] = []  # app-created folders files.list can see
        self.token_scope: str | None = None  # scope echoed by the token endpoint
        self.server_scope_error = False
        self.docs_disabled = False
        self.next_id = 0

    def _id(self, prefix: str) -> str:
        self.next_id += 1
        return f"{prefix}_{self.next_id}"

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host != "oauth2.googleapis.com" and r.method == "POST"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        path = request.url.path
        if url.startswith(google_auth.TOKEN_URL):
            payload = {"access_token": "at_live_token", "refresh_token": "rt_live_token", "expires_in": 3600}
            if self.token_scope:
                payload["scope"] = self.token_scope
            return httpx.Response(200, json=payload)
        if request.headers.get("Authorization") != "Bearer at_live_token":
            return httpx.Response(401, json={"error": {"message": "invalid"}})
        if path == "/calendar/v3/calendars/primary":
            return httpx.Response(200, json={"id": "me@example.com"})
        if self.server_scope_error:
            return httpx.Response(
                403,
                json={"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes.",
                                "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}},
            )
        if request.url.host == "docs.googleapis.com" and self.docs_disabled:
            return httpx.Response(
                403,
                json={"error": {"code": 403, "status": "PERMISSION_DENIED",
                                "message": "Google Docs API has not been used in project 123 before or it is disabled.",
                                "details": [{"reason": "SERVICE_DISABLED", "metadata": {"service": "docs.googleapis.com"}}]}},
            )
        if path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": [{"id": fid, "name": "Escort"} for fid in self.listable_folders]})
        if path == "/drive/v3/files" and request.method == "POST":
            body = json.loads(request.content)
            if body["mimeType"] == FOLDER_MIME:
                fid = self._id("folder")
                self.folders[fid] = body
                return httpx.Response(200, json={"id": fid})
            assert body["mimeType"] == DOC_MIME
            did = self._id("doc")
            self.docs[did] = 2
            return httpx.Response(200, json={"id": did, "name": body["name"], "webViewLink": f"https://docs.google.com/document/d/{did}/edit"})
        if path.startswith("/drive/v3/files/"):
            fid = path.rsplit("/", 1)[-1]
            if fid in self.folders:
                return httpx.Response(200, json={"id": fid, "trashed": False, "mimeType": FOLDER_MIME})
            return httpx.Response(404, json={"error": {"message": "File not found"}})
        if path.startswith("/v1/documents/") and path.endswith(":batchUpdate"):
            did = path.removeprefix("/v1/documents/").removesuffix(":batchUpdate")
            if did not in self.docs:
                return httpx.Response(404, json={"error": {"message": "Requested entity was not found."}})
            for item in json.loads(request.content)["requests"]:
                if "insertText" in item:
                    self.docs[did] += len(item["insertText"]["text"].encode("utf-16-le")) // 2
            return httpx.Response(200, json={"documentId": did, "replies": []})
        if path.startswith("/v1/documents/"):
            did = path.removeprefix("/v1/documents/")
            if did not in self.docs:
                return httpx.Response(404, json={"error": {"message": "Requested entity was not found."}})
            return httpx.Response(200, json={"body": {"content": [{"endIndex": 1}, {"endIndex": self.docs[did]}]}})
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _setup(scopes=SCOPES, fake=None, opened=None):
    fake = fake or FakeWorkspace()
    secrets = MemorySecretStore()
    connectors = Connectors(
        secrets,
        client_id_getter=lambda: "client-123.apps.googleusercontent.com",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        open_browser=(opened.append if opened is not None else (lambda _url: None)),
    )
    if scopes is not None:
        secrets.set(
            google_auth.TOKEN_KEY,
            json.dumps({"access_token": "at_live_token", "refresh_token": "rt_live_token", "expires_at": time.time() + 3600,
                        "scope": " ".join(scopes), "account": "me@example.com"}),
        )
    return connectors, fake, secrets


def _runtime(tmp_path: Path, connectors, secrets, save_notes=True) -> tuple[DesktopRuntime, TestClient]:
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors, monitor_lister=lambda: [])
    runtime.prefs["google_save_notes"] = save_notes
    runtime.store.title = "BayaniPay review"
    return runtime, TestClient(create_desktop_app(runtime))


def _commitment(runtime: DesktopRuntime, card_id="card_1") -> None:
    runtime.store.add_card(
        CardRecord(id=card_id, type="COMMITMENT", title="Me: timeline", body="", prd_section=None, who="Me",
                   what="Send the revised timeline", utterance_id=None, trigger_text=None, created_at="", latency_ms=0,
                   crop_path=None, crop_url=None, source="test", deictic=None, segmenter=None, when="Friday")
    )


READY_SUMMARY = {
    "status": "ready",
    "title": "Scope review",
    "bullets": ["Phase 2 waits for KYC."],
    "commitments": [{"who": "Me", "what": "Send timeline", "due": "Friday"}],
    "conflicts": [{"title": "Fee change", "section": "SOW: Fees"}],
}


# ---- incremental consent ----------------------------------------------------


def test_incremental_consent_url_asks_only_for_narrow_write_scopes():
    connectors, _fake, _secrets = _setup()
    flow = connectors.google.prepare("http://127.0.0.1:5555/", scopes=WRITE_SCOPES)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow.auth_url).query))
    assert query["scope"].split() == list(WRITE_SCOPES)
    assert set(WRITE_SCOPES) == {"https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/documents"}
    assert FULL_DRIVE not in query["scope"].split()
    assert query["include_granted_scopes"] == "true"
    assert query["code_challenge_method"] == "S256" and query["state"] == flow.state
    assert query["login_hint"] == "me@example.com"


def test_turning_on_save_notes_starts_incremental_consent(tmp_path: Path):
    opened: list[str] = []
    connectors, _fake, secrets = _setup(opened=opened)
    _runtime_, client = _runtime(tmp_path, connectors, secrets, save_notes=False)
    out = client.post("/api/connectors/google/notes", json={"enabled": True}).json()
    assert opened == [out["auth_url"]]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(out["auth_url"]).query))
    assert query["scope"].split() == list(WRITE_SCOPES) and query["include_granted_scopes"] == "true"
    google = out["providers"][0]
    assert google["save_notes"] is True and google["needs_consent"] is True and google["consent_pending"] is True
    assert google["capabilities"]["read_docs"] is True and google["capabilities"]["write_notes"] is False
    assert google["consent"]["notes"] == "pending"
    connectors.google.cancel()


def test_granted_scopes_are_persisted_after_incremental_consent():
    connectors, _fake, secrets = _setup()
    flow = connectors.google.prepare("http://127.0.0.1:5555/", scopes=WRITE_SCOPES)
    status, html = connectors.google.handle_callback({"code": "c0de", "state": flow.state})
    assert status == 200 and "save notes" in html
    stored = json.loads(secrets.get(google_auth.TOKEN_KEY))
    assert set(stored["scopes"]) == set(SCOPES) | set(WRITE_SCOPES)
    assert stored["account"] == "me@example.com"
    row = connectors.status()["providers"][0]
    assert row["capabilities"]["write_notes"] is True


def test_scope_echoed_by_google_is_authoritative():
    fake = FakeWorkspace()
    fake.token_scope = " ".join(SCOPES)  # user unticked the write boxes on Google's screen
    connectors, _fake, secrets = _setup(fake=fake)
    flow = connectors.google.prepare("http://127.0.0.1:5555/", scopes=WRITE_SCOPES)
    status, html = connectors.google.handle_callback({"code": "c0de", "state": flow.state})
    assert status == 200 and "not allowed" in html
    assert set(json.loads(secrets.get(google_auth.TOKEN_KEY))["scopes"]) == set(SCOPES)
    assert connectors.google.can_write_notes() is False
    assert "did not grant note saving" in connectors.status()["providers"][0]["setup_hint"]


# ---- re-consent and API errors ----------------------------------------------


def test_write_without_scope_returns_reconsent_error_and_calls_nothing(tmp_path: Path):
    connectors, fake, secrets = _setup()
    runtime, client = _runtime(tmp_path, connectors, secrets)
    runtime.summary = dict(READY_SUMMARY)
    response = client.post("/api/notes/drive", json={"kind": "summary"})
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "needs_consent" and "Allow note saving" in body["error"]
    assert fake.requests == []
    assert client.get("/api/notes/drive").json()["needs_consent"] is True
    assert client.get("/api/connectors/status").json()["providers"][0]["needs_consent"] is True


def test_server_side_insufficient_scope_maps_to_reconsent(tmp_path: Path):
    fake = FakeWorkspace()
    fake.server_scope_error = True
    connectors, _fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES, fake=fake)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    _commitment(runtime)
    response = client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "card_1"})
    assert response.status_code == 403 and response.json()["code"] == "needs_consent"


def test_docs_api_disabled_says_how_to_enable_it(tmp_path: Path):
    fake = FakeWorkspace()
    fake.docs_disabled = True
    connectors, _fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES, fake=fake)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    runtime.summary = dict(READY_SUMMARY)
    response = client.post("/api/notes/drive", json={"kind": "summary"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "api_disabled"
    assert "Enable the Google Docs API in your Google Cloud project" in body["error"]
    assert body["help_url"] == "https://console.cloud.google.com/apis/library/docs.googleapis.com"


def test_not_connected_and_notes_off_errors(tmp_path: Path):
    connectors, _fake, secrets = _setup(scopes=None)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    _commitment(runtime)
    response = client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "card_1"})
    assert response.status_code == 401 and response.json()["code"] == "not_connected"
    runtime.prefs["google_save_notes"] = False
    off = client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "card_1"})
    assert off.status_code == 400 and off.json()["code"] == "notes_off"
    assert off.json()["error"] == "Turn on Save notes to Drive to keep this in Google Docs."


def test_turning_off_only_stops_using_it(tmp_path: Path):
    connectors, fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    out = client.post("/api/connectors/google/notes", json={"enabled": False}).json()
    assert out["revoke_url"] == "https://myaccount.google.com/permissions"
    assert out["providers"][0]["save_notes"] is False and out["providers"][0]["capabilities"]["write_notes"] is True
    assert fake.requests == [] and connectors.google.connected()
    assert json.loads((tmp_path / "config.json").read_text())["google_save_notes"] is False


# ---- Drive folder + doc ------------------------------------------------------


def _notes(fake=None, state=None):
    connectors, fake, _secrets = _setup(scopes=SCOPES + WRITE_SCOPES, fake=fake)
    return GoogleNotes(connectors.google, DictNotesState(state if state is not None else {})), fake


def test_folder_is_created_once_then_reused():
    prefs: dict = {}
    notes, fake = _notes(state=prefs)
    folder = notes.ensure_folder()
    created = [json.loads(r.content) for r in fake.writes()]
    assert created == [{"name": "Escort", "mimeType": FOLDER_MIME, "appProperties": {"escort": "notes"}}]
    assert prefs["google_notes_folder_id"] == folder
    assert notes.ensure_folder() == folder
    assert len(fake.writes()) == 1


def test_existing_app_folder_is_found_when_prefs_forgot_it():
    fake = FakeWorkspace()
    fake.listable_folders = ["folder_old"]
    prefs = {"google_notes_folder_id": "folder_trashed_or_gone"}
    notes, fake = _notes(fake=fake, state=prefs)
    assert notes.ensure_folder() == "folder_old"
    assert fake.writes() == [] and prefs["google_notes_folder_id"] == "folder_old"
    listing = next(r for r in fake.requests if r.url.path == "/drive/v3/files" and r.method == "GET")
    assert "appProperties has { key='escort' and value='notes' }" in listing.url.params["q"]


def test_meeting_doc_is_created_in_the_folder_and_registered():
    prefs: dict = {}
    notes, fake = _notes(state=prefs)
    entry = notes.ensure_meeting_doc("mtg_1", "BayaniPay review")
    body = json.loads(fake.writes()[-1].content)
    folder = prefs["google_notes_folder_id"]
    assert body["mimeType"] == DOC_MIME and body["parents"] == [folder]
    assert body["name"] == f"Escort · BayaniPay review · {datetime.now():%Y-%m-%d}"
    assert entry["url"] == f"https://docs.google.com/document/d/{entry['doc_id']}/edit"
    assert prefs["google_notes_docs"]["mtg_1"]["doc_id"] == entry["doc_id"]
    assert notes.ensure_meeting_doc("mtg_1", "BayaniPay review") == entry
    assert sum(1 for r in fake.writes() if json.loads(r.content).get("mimeType") == DOC_MIME) == 1


def test_doc_title_format():
    assert doc_title("  Weekly   sync ", datetime(2026, 11, 2)) == "Escort · Weekly sync · 2026-11-02"
    assert doc_title("", datetime(2026, 11, 2)) == "Escort · Meeting · 2026-11-02"


def test_append_text_request_body():
    notes, fake = _notes()
    entry = notes.ensure_meeting_doc("mtg_1", "Sync")
    notes.append_text(entry["doc_id"], "- Me: send it (Friday)")
    update = fake.requests[-1]
    assert update.url.path == f"/v1/documents/{entry['doc_id']}:batchUpdate"
    assert json.loads(update.content) == {
        "requests": [{"insertText": {"endOfSegmentLocation": {"segmentId": ""}, "text": "- Me: send it (Friday)\n"}}]
    }


def test_refuses_to_touch_a_doc_escort_did_not_create():
    notes, fake = _notes()
    with pytest.raises(UnknownDocError):
        notes.append_text("someone_elses_doc", "hello")
    with pytest.raises(UnknownDocError):
        notes.append_structured("someone_elses_doc", [("x", "NORMAL_TEXT")])
    assert fake.requests == []


def test_deleted_meeting_doc_is_recreated_once():
    prefs: dict = {}
    notes, fake = _notes(state=prefs)
    first = notes.ensure_meeting_doc("mtg_1", "Sync")
    del fake.docs[first["doc_id"]]  # the user deleted it in Drive
    entry = notes.note_commitment("mtg_1", "Sync", "Me", "Ship", "Friday")
    assert entry["doc_id"] != first["doc_id"]
    assert prefs["google_notes_docs"]["mtg_1"]["doc_id"] == entry["doc_id"]


def test_structured_requests_style_headings_with_utf16_ranges():
    blocks = summary_blocks(READY_SUMMARY, "2026-11-02 10:00")
    texts = [line for line, _ in blocks]
    assert texts[0] == "Recap · 2026-11-02 10:00"
    assert [line for line, style in blocks if style == "HEADING_2"] == ["Summary", "Commitments", "Conflicts"]
    assert "- Me: Send timeline (Friday)" in texts and "- Fee change (SOW: Fees)" in texts
    requests = structured_requests([("Héllo 👋", "HEADING_1"), ("body", "NORMAL_TEXT")], start=5)
    assert requests[0]["insertText"] == {"endOfSegmentLocation": {"segmentId": ""}, "text": "Héllo 👋\nbody\n"}
    assert requests[1]["updateParagraphStyle"]["range"] == {"startIndex": 5, "endIndex": 5 + 9 + 5}
    heading = requests[2]["updateParagraphStyle"]
    assert heading["range"] == {"startIndex": 5, "endIndex": 5 + 9} and heading["paragraphStyle"] == {"namedStyleType": "HEADING_1"}


def test_scope_guard_without_write_scopes():
    connectors, fake, _secrets = _setup()
    notes = GoogleNotes(connectors.google, DictNotesState({}))
    with pytest.raises(ConnectorScopeError):
        notes.ensure_folder()
    assert fake.requests == []


# ---- user-clicked actions through the API -------------------------------------


def test_note_it_appends_commitment_to_the_meeting_doc(tmp_path: Path):
    connectors, fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    _commitment(runtime, "card_1")
    _commitment(runtime, "card_2")
    first = client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "card_1"})
    assert first.status_code == 200
    doc = first.json()["doc"]
    assert first.json()["ok"] is True and doc["title"].startswith("Escort · BayaniPay review · ")
    assert set(doc) == {"id", "url", "title", "created_at"}
    update = json.loads(fake.requests[-1].content)
    assert update["requests"][0]["insertText"]["text"] == "- Me: Send the revised timeline (Friday)\n"
    second = client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "card_2"}).json()
    assert second["doc"]["id"] == doc["id"]
    current = client.get("/api/notes/drive").json()
    assert current["doc"]["id"] == doc["id"] and current["noted_cards"] == ["card_1", "card_2"]
    assert client.get("/api/status").json()["notes_drive"]["doc"]["url"] == doc["url"]
    bad = client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "nope"})
    assert bad.status_code == 400 and bad.json()["code"] == "unknown_card"


def test_summary_save_appends_a_dated_section_each_time(tmp_path: Path):
    connectors, fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    assert client.post("/api/notes/drive", json={"kind": "summary"}).json()["code"] == "no_summary"
    runtime.summary = dict(READY_SUMMARY)
    first = client.post("/api/notes/drive", json={"kind": "summary"}).json()
    second = client.post("/api/notes/drive", json={"kind": "summary"}).json()
    assert first["doc"]["id"] == second["doc"]["id"] and second["summary_saved_at"]
    updates = [json.loads(r.content) for r in fake.requests if r.url.path.endswith(":batchUpdate")]
    assert len(updates) == 2
    for update in updates:
        text = update["requests"][0]["insertText"]["text"]
        assert text.startswith("Recap · ") and "\nSummary\n" in text and "\nCommitments\n" in text and "\nConflicts\n" in text
        assert "endOfSegmentLocation" in update["requests"][0]["insertText"]
    # The second section starts where the first one ended: appended, never overwritten.
    first_len = len(updates[0]["requests"][0]["insertText"]["text"].encode("utf-16-le")) // 2
    assert updates[0]["requests"][1]["updateParagraphStyle"]["range"]["startIndex"] == 1
    assert updates[1]["requests"][1]["updateParagraphStyle"]["range"]["startIndex"] == 1 + first_len
    assert not any("deleteContentRange" in item for update in updates for item in update["requests"])


def test_new_meeting_gets_a_new_doc(tmp_path: Path):
    connectors, _fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    runtime.summary = dict(READY_SUMMARY)
    first = client.post("/api/notes/drive", json={"kind": "summary"}).json()["doc"]["id"]
    runtime.delete_meeting()
    assert client.get("/api/notes/drive").json()["doc"] is None
    runtime.summary = dict(READY_SUMMARY)
    assert client.post("/api/notes/drive", json={"kind": "summary"}).json()["doc"]["id"] != first


def test_consent_endpoint_requires_connection(tmp_path: Path):
    connectors, _fake, secrets = _setup(scopes=None)
    _runtime_, client = _runtime(tmp_path, connectors, secrets)
    response = client.post("/api/connectors/google/consent")
    assert response.status_code == 400 and response.json()["code"] == "not_connected"


# ---- chat: save only after an explicit yes -------------------------------------


def test_chat_save_summary_needs_confirmation(tmp_path: Path):
    connectors, fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, _client = _runtime(tmp_path, connectors, secrets)
    runtime.summary = dict(READY_SUMMARY)
    asked = runtime.context_chat("save summary to Docs")
    assert "Reply yes" in asked["reply"] and fake.writes() == []
    declined = runtime.context_chat("no")
    assert "won't save" in declined["reply"] and fake.writes() == []
    runtime.context_chat("please save this to my notes")
    saved = runtime.context_chat("yes")
    assert saved["reply"].startswith("Saved to Google Docs: Escort · BayaniPay review")
    assert saved["changed"][0]["setting"] == "notes_drive"
    assert any(r.url.path.endswith(":batchUpdate") for r in fake.requests)


def test_chat_yes_without_a_pending_question_saves_nothing(tmp_path: Path, monkeypatch):
    connectors, fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, _client = _runtime(tmp_path, connectors, secrets)
    runtime.summary = dict(READY_SUMMARY)
    monkeypatch.setattr("escort.desktop.runtime.prep_chat", lambda *a, **k: "muse answer")
    assert runtime.context_chat("yes")["reply"] == "muse answer"
    assert fake.writes() == []


def test_chat_save_when_notes_off_points_to_the_toggle(tmp_path: Path):
    connectors, fake, secrets = _setup(scopes=SCOPES + WRITE_SCOPES)
    runtime, _client = _runtime(tmp_path, connectors, secrets, save_notes=False)
    runtime.summary = dict(READY_SUMMARY)
    reply = runtime.context_chat("save summary to google docs")["reply"]
    assert "Note saving is off" in reply and fake.writes() == []


# ---- nothing secret leaves the backend -------------------------------------------


def test_client_secret_and_tokens_never_in_responses_or_logs(tmp_path: Path, caplog):
    caplog.set_level(logging.DEBUG)
    opened: list[str] = []
    connectors, _fake, secrets = _setup(opened=opened)
    runtime, client = _runtime(tmp_path, connectors, secrets, save_notes=False)
    _commitment(runtime)
    runtime.summary = dict(READY_SUMMARY)
    texts = [client.post("/api/connectors/google/notes", json={"enabled": True}).text]
    flow = connectors.google._flow
    connectors.google.handle_callback({"code": "c0de", "state": flow.state})
    texts.append(client.post("/api/notes/drive", json={"kind": "commitment", "card_id": "card_1"}).text)
    texts.append(client.post("/api/notes/drive", json={"kind": "summary"}).text)
    texts += [client.get(path).text for path in ("/api/notes/drive", "/api/connectors/status", "/api/status")]
    texts.append((tmp_path / "config.json").read_text())
    for secret in (CLIENT_SECRET, "at_live_token", "rt_live_token"):
        assert all(secret not in text for text in texts), secret
        assert secret not in caplog.text
        assert all(secret not in url for url in opened)
