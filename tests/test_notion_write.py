"""Save notes to Notion: child pages under a chosen parent, append-only, user-clicked (no network)."""

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources.base import ConnectorScopeError
from escort.context.sources.notion_source import TOKEN_KEY
from escort.context.sources.notion_write import (
    NotionNotes,
    NotionNotesState,
    UnknownPageError,
    summary_children,
)
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.models import CardRecord
from escort.store import MeetingStore

TOKEN = "ntn_super_secret_integration_token"
PARENT = "1111aaaa-2222-bbbb-3333-cccc4444dddd"
READY_SUMMARY = {
    "status": "ready",
    "title": "Scope review",
    "bullets": ["Phase 2 waits for KYC."],
    "commitments": [{"who": "Me", "what": "Send timeline", "due": "Friday"}],
    "conflicts": [{"title": "Fee change", "section": "SOW: Fees"}],
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ESCORT_CONNECTORS", raising=False)


class FakeNotion:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pages: dict[str, list] = {}
        self.restricted = False
        self.next_id = 0

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method in ("POST", "PATCH") and r.url.path != "/v1/search"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"code": "unauthorized"})
        path = request.url.path
        if self.restricted and request.method in ("POST", "PATCH"):
            return httpx.Response(403, json={"object": "error", "status": 403, "code": "restricted_resource", "message": "Insufficient permissions"})
        if path == "/v1/pages" and request.method == "POST":
            body = json.loads(request.content)
            if body["parent"]["page_id"] != PARENT:
                return httpx.Response(404, json={"code": "object_not_found"})
            self.next_id += 1
            pid = f"page-{self.next_id}"
            self.pages[pid] = []
            return httpx.Response(200, json={"object": "page", "id": pid, "url": f"https://www.notion.so/{pid}"})
        if path.startswith("/v1/blocks/") and path.endswith("/children") and request.method == "PATCH":
            pid = path.split("/")[3]
            if pid not in self.pages:
                return httpx.Response(404, json={"code": "object_not_found"})
            self.pages[pid] += json.loads(request.content)["children"]
            return httpx.Response(200, json={"results": []})
        if path == "/v1/search":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"code": "object_not_found"})


def _setup(fake=None, connected=True):
    fake = fake or FakeNotion()
    secrets = MemorySecretStore()
    if connected:
        secrets.set(TOKEN_KEY, json.dumps({"token": TOKEN, "account": "Acme"}))
    return Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(fake))), fake, secrets


def _notes(state=None, fake=None):
    connectors, fake, _secrets = _setup(fake=fake)
    data = state if state is not None else {"notion_notes_parent": {"id": PARENT, "title": "Meetings"}}
    return NotionNotes(connectors.notion, NotionNotesState(data)), fake, data


def _runtime(tmp_path: Path, connectors, secrets, enabled=True, parent=True):
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors, monitor_lister=lambda: [])
    runtime.store.title = "BayaniPay review"
    client = TestClient(create_desktop_app(runtime))
    client.post("/api/connectors/notion/notes", json={"enabled": enabled, **({"parent_id": PARENT, "parent_title": "Meetings"} if parent else {})})
    return runtime, client


def _commitment(runtime, card_id="card_1"):
    runtime.store.add_card(
        CardRecord(id=card_id, type="COMMITMENT", title="t", body="", prd_section=None, who="Me", what="Send the revised timeline",
                   utterance_id=None, trigger_text=None, created_at="", latency_ms=0, crop_path=None, crop_url=None,
                   source="test", deictic=None, segmenter=None, when="Friday")
    )


def _text(block):
    return block[block["type"]]["rich_text"][0]["text"]["content"]


def test_creates_child_page_under_parent_and_registers_it():
    notes, fake, data = _notes()
    entry = notes.ensure_meeting_page("mtg_1", "BayaniPay review")
    body = json.loads(fake.writes()[0].content)
    assert body["parent"] == {"page_id": PARENT}
    assert body["properties"]["title"]["title"][0]["text"]["content"] == f"Escort · BayaniPay review · {datetime.now():%Y-%m-%d}"
    assert data["notion_notes_pages"]["mtg_1"]["page_id"] == entry["page_id"] == "page-1"
    assert notes.ensure_meeting_page("mtg_1", "BayaniPay review") == entry
    assert len(fake.writes()) == 1


def test_append_blocks_uses_patch_children():
    notes, fake, _data = _notes()
    entry = notes.note_commitment("mtg_1", "Sync", "Me", "Ship it", "Friday")
    patch = fake.requests[-1]
    assert patch.method == "PATCH" and patch.url.path == f"/v1/blocks/{entry['page_id']}/children"
    children = json.loads(patch.content)["children"]
    assert children == [
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "Me: Ship it (Friday)"}}]}}
    ]


def test_summary_children_are_headings_bullets_paragraphs():
    blocks = summary_children(READY_SUMMARY, "2026-11-02 10:00")
    assert [(b["type"], _text(b)) for b in blocks] == [
        ("heading_2", "Recap · 2026-11-02 10:00"),
        ("heading_3", "Summary"),
        ("bulleted_list_item", "Phase 2 waits for KYC."),
        ("heading_3", "Commitments"),
        ("bulleted_list_item", "Me: Send timeline (Friday)"),
        ("heading_3", "Conflicts"),
        ("bulleted_list_item", "Fee change (SOW: Fees)"),
    ]
    empty = summary_children({"bullets": [], "commitments": [], "conflicts": []}, "x")
    assert [b["type"] for b in empty].count("paragraph") == 3


def test_refuses_pages_escort_did_not_create():
    notes, fake, _data = _notes()
    with pytest.raises(UnknownPageError):
        notes.append_blocks("someone-elses-page", [])
    assert fake.requests == []
    notes.append_blocks(PARENT.replace("-", ""), [])  # the chosen parent is allowed (id format-insensitive)


def test_restricted_resource_is_a_clear_capability_error():
    fake = FakeNotion()
    fake.restricted = True
    notes, _fake, _data = _notes(fake=fake)
    with pytest.raises(ConnectorScopeError) as caught:
        notes.ensure_meeting_page("mtg_1", "Sync")
    assert caught.value.code == "needs_capabilities"
    assert "Insert content" in str(caught.value) and "Update content" in str(caught.value)


def test_requires_a_parent_page():
    notes, fake, _data = _notes(state={})
    with pytest.raises(Exception) as caught:
        notes.ensure_meeting_page("mtg_1", "Sync")
    assert caught.value.code == "no_parent" and fake.requests == []


def test_deleted_page_is_recreated_once():
    notes, fake, data = _notes()
    first = notes.ensure_meeting_page("mtg_1", "Sync")
    del fake.pages[first["page_id"]]
    entry = notes.note_commitment("mtg_1", "Sync", "Me", "Ship", None)
    assert entry["page_id"] != first["page_id"] and data["notion_notes_pages"]["mtg_1"]["page_id"] == entry["page_id"]


# ---- API ----------------------------------------------------------------------


def test_notion_card_status_and_parent_choice(tmp_path: Path):
    connectors, _fake, secrets = _setup()
    _runtime_, client = _runtime(tmp_path, connectors, secrets, enabled=True, parent=False)
    row = client.get("/api/connectors/status").json()["providers"][1]
    assert row["save_notes"] is True and row["needs_parent"] is True and row["capabilities"] == {"read": True, "write_notes": False}
    out = client.post("/api/connectors/notion/notes", json={"enabled": True, "parent_id": PARENT, "parent_title": "Meetings"}).json()
    row = out["providers"][1]
    assert row["parent"] == {"id": PARENT, "title": "Meetings"} and row["capabilities"]["write_notes"] is True
    assert json.loads((tmp_path / "config.json").read_text())["notion_notes_parent"]["id"] == PARENT


def test_note_it_and_summary_to_notion(tmp_path: Path):
    connectors, fake, secrets = _setup()
    runtime, client = _runtime(tmp_path, connectors, secrets)
    _commitment(runtime)
    noted = client.post("/api/notes/notion", json={"kind": "commitment", "card_id": "card_1"})
    assert noted.status_code == 200 and noted.json()["destination"] == "notion"
    page_id = noted.json()["doc"]["id"]
    assert _text(fake.pages[page_id][0]) == "Me: Send the revised timeline (Friday)"
    runtime.summary = dict(READY_SUMMARY)
    client.post("/api/notes/notion", json={"kind": "summary"})
    client.post("/api/notes/notion", json={"kind": "summary"})
    headings = [_text(b) for b in fake.pages[page_id] if b["type"] == "heading_2"]
    assert len(headings) == 2 and all(h.startswith("Recap · ") for h in headings)  # appended, never overwritten
    status = client.get("/api/notes/notion").json()
    assert status["doc"]["id"] == page_id and status["noted_cards"] == ["card_1"] and status["summary_saved_at"]
    assert client.get("/api/notes/destinations").json()["last"] == "notion"


def test_restricted_resource_via_api_sets_needs_capabilities(tmp_path: Path):
    fake = FakeNotion()
    fake.restricted = True
    connectors, _fake, secrets = _setup(fake=fake)
    runtime, client = _runtime(tmp_path, connectors, secrets)
    runtime.summary = dict(READY_SUMMARY)
    response = client.post("/api/notes/notion", json={"kind": "summary"})
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "needs_capabilities" and body["help_url"] == "https://www.notion.so/profile/integrations"
    assert client.get("/api/connectors/status").json()["providers"][1]["needs_capabilities"] is True
    assert connectors.notion.connected()  # a capability error never disconnects


def test_notion_notes_off_and_not_connected(tmp_path: Path):
    connectors, _fake, secrets = _setup(connected=False)
    runtime, client = _runtime(tmp_path, connectors, secrets, enabled=False)
    _commitment(runtime)
    off = client.post("/api/notes/notion", json={"kind": "commitment", "card_id": "card_1"})
    assert off.status_code == 400 and off.json()["code"] == "notes_off"
    client.post("/api/connectors/notion/notes", json={"enabled": True})
    missing = client.post("/api/notes/notion", json={"kind": "commitment", "card_id": "card_1"})
    assert missing.status_code == 401 and missing.json()["code"] == "not_connected"


def test_last_destination_is_remembered_and_used_by_chat(tmp_path: Path):
    connectors, fake, secrets = _setup()
    runtime, client = _runtime(tmp_path, connectors, secrets)
    runtime.prefs["google_save_notes"] = True  # both enabled; Google is not connected here
    runtime.summary = dict(READY_SUMMARY)
    assert client.get("/api/notes/destinations").json()["enabled"] == ["drive", "notion"]
    client.post("/api/notes/notion", json={"kind": "summary"})
    assert runtime.preferred_destination() == "notion"
    asked = runtime.context_chat("save summary to my notes")["reply"]
    assert "Save the meeting summary to Notion?" in asked
    saved = runtime.context_chat("yes")
    assert saved["reply"].startswith("Saved to Notion: Escort · BayaniPay review")
    assert len([b for b in next(iter(fake.pages.values())) if b["type"] == "heading_2"]) == 2


def test_token_never_in_responses_or_logs(tmp_path: Path, caplog):
    caplog.set_level(logging.DEBUG)
    connectors, _fake, secrets = _setup()
    runtime, client = _runtime(tmp_path, connectors, secrets)
    _commitment(runtime)
    runtime.summary = dict(READY_SUMMARY)
    texts = [
        client.post("/api/notes/notion", json={"kind": "commitment", "card_id": "card_1"}).text,
        client.post("/api/notes/notion", json={"kind": "summary"}).text,
        client.get("/api/notes/notion").text,
        client.get("/api/status").text,
        (tmp_path / "config.json").read_text(),
    ]
    assert all(TOKEN not in text for text in texts)
    assert TOKEN not in caplog.text
