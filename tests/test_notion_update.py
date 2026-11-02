"""Update existing Notion pages: picked targets only, read first, targeted edits, preview then confirm."""

import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from escort.context.sources.base import ConnectorScopeError
from escort.context.sources.notion_source import TOKEN_KEY
from escort.context.sources.notion_write import NotionNotes, NotionNotesState, UnknownPageError
from escort.context.sources.registry import Connectors
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.models import CardRecord
from escort.store import MeetingStore

TOKEN = "ntn_super_secret_integration_token"
PARENT = "1111aaaa-2222-bbbb-3333-cccc4444dddd"
CRM = "aaaa1111-0000-4000-8000-000000000001"
OTHER = "bbbb2222-0000-4000-8000-000000000002"
READY_SUMMARY = {
    "status": "ready",
    "title": "Scope review",
    "bullets": ["Phase 2 waits for KYC."],
    "commitments": [{"who": "Pia", "what": "Send SOW", "due": "Friday"}],
    "conflicts": [],
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ESCORT_CONNECTORS", raising=False)


def _rich(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def _block(bid, kind, text, **extra):
    return {"object": "block", "id": bid, "type": kind, kind: {"rich_text": _rich(text), **extra}}


def crm_blocks():
    return [
        _block("b-h1", "heading_2", "Account notes"),
        _block("b-p1", "paragraph", "Acme is a design partner."),
        _block("b-h2", "heading_2", "Commitments"),
        _block("b-c1", "bulleted_list_item", "Ana: intro call (Monday)"),
        _block("b-c2", "bulleted_list_item", "Lea: pricing sheet (Tuesday)"),
        _block("b-h3", "heading_2", "Risks"),
        _block("b-r1", "bulleted_list_item", "Budget freeze"),
    ]


class FakeNotion:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pages: dict[str, list[dict]] = {CRM: crm_blocks(), OTHER: [_block("o-1", "paragraph", "Private page")]}
        self.restricted = False

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method in ("POST", "PATCH")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"code": "unauthorized"})
        path = request.url.path
        if self.restricted and request.method in ("POST", "PATCH"):
            return httpx.Response(403, json={"object": "error", "status": 403, "code": "restricted_resource", "message": "Insufficient permissions"})
        parts = path.split("/")  # ['', 'v1', 'blocks', id, 'children']
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            page = parts[3]
            if page not in self.pages:
                return httpx.Response(404, json={"code": "object_not_found"})
            if request.method == "GET":
                return httpx.Response(200, json={"results": self.pages[page], "has_more": False, "next_cursor": None})
            body = json.loads(request.content)
            children = [{**item, "id": f"new-{len(self.pages[page]) + i}", item["type"]: {**item[item["type"]], "rich_text": _rich(item[item["type"]]["rich_text"][0]["text"]["content"])}} for i, item in enumerate(body["children"])]
            after = body.get("after")
            ids = [b["id"] for b in self.pages[page]]
            index = ids.index(after) + 1 if after in ids else len(ids)
            self.pages[page][index:index] = children
            return httpx.Response(200, json={"results": children})
        if path.startswith("/v1/blocks/") and request.method == "PATCH":
            block_id = parts[3]
            for blocks in self.pages.values():
                for item in blocks:
                    if item["id"] == block_id:
                        payload = json.loads(request.content)[item["type"]]
                        item[item["type"]]["rich_text"] = _rich(payload["rich_text"][0]["text"]["content"])
                        return httpx.Response(200, json=item)
            return httpx.Response(404, json={"code": "object_not_found"})
        return httpx.Response(404, json={"code": "object_not_found"})


def _setup(fake=None):
    fake = fake or FakeNotion()
    secrets = MemorySecretStore()
    secrets.set(TOKEN_KEY, json.dumps({"token": TOKEN, "account": "Workspace"}))
    return Connectors(secrets, http=httpx.Client(transport=httpx.MockTransport(fake))), fake, secrets


def _notes(targets=(CRM,)):
    connectors, fake, _s = _setup()
    data = {"notion_notes_parent": {"id": PARENT, "title": "Meetings"}}
    state = NotionNotesState(data)
    for target in targets:
        state.add_target(target, "CRM notes")
    return NotionNotes(connectors.notion, state), fake, data


def _text(block):
    return block[block["type"]]["rich_text"][0]["text"]["content"]


def _runtime(tmp_path: Path, fake=None, picked=True):
    connectors, fake, secrets = _setup(fake)
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), secrets, tmp_path / "config.json", connectors=connectors, monitor_lister=lambda: [])
    client = TestClient(create_desktop_app(runtime))
    client.post("/api/connectors/notion/notes", json={"enabled": True, "parent_id": PARENT, "parent_title": "Meetings"})
    if picked:
        client.post("/api/connectors/notion/targets", json={"add": {"id": CRM, "title": "CRM notes"}})
    runtime.summary = dict(READY_SUMMARY)
    runtime.store.add_card(
        CardRecord(id="card_1", type="COMMITMENT", title="t", body="", prd_section=None, who="Pia", what="send SOW by Friday",
                   utterance_id=None, trigger_text=None, created_at="", latency_ms=0, crop_path=None, crop_url=None,
                   source="test", deictic=None, segmenter=None, when=None)
    )
    return runtime, client, fake


# ---- helpers: read first, then targeted edits -------------------------------


def test_read_page_returns_block_text_and_levels_before_any_write():
    notes, fake, _ = _notes()
    blocks = notes.read_page(CRM)
    assert [b["text"] for b in blocks][:3] == ["Account notes", "Acme is a design partner.", "Commitments"]
    assert blocks[0]["level"] == 2 and blocks[3]["line"] == "- Ana: intro call (Monday)"
    assert fake.writes() == []


def test_edit_block_text_patches_only_that_block():
    notes, fake, _ = _notes()
    result = notes.edit_block_text(CRM, "b-c1", "Ana: intro call (done)")
    patch = fake.writes()[-1]
    assert patch.method == "PATCH" and patch.url.path == "/v1/blocks/b-c1"
    assert json.loads(patch.content) == {"bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "Ana: intro call (done)"}}]}}
    assert result["before"] == "Ana: intro call (Monday)"
    assert [_text(b) for b in fake.pages[CRM]][3:5] == ["Ana: intro call (done)", "Lea: pricing sheet (Tuesday)"]
    assert len(fake.writes()) == 1


def test_edit_refuses_a_block_that_is_not_on_the_page():
    notes, fake, _ = _notes()
    with pytest.raises(Exception) as caught:
        notes.edit_block_text(CRM, "o-1", "hijack")  # a block of another page
    assert getattr(caught.value, "code", None) == "block_not_found"
    assert fake.writes() == []


def test_add_item_goes_at_the_end_of_the_named_section():
    notes, fake, _ = _notes()
    notes.add_item_under_heading(CRM, "commitments", "Pia: send SOW by Friday")
    assert [_text(b) for b in fake.pages[CRM]] == [
        "Account notes", "Acme is a design partner.", "Commitments", "Ana: intro call (Monday)",
        "Lea: pricing sheet (Tuesday)", "Pia: send SOW by Friday", "Risks", "Budget freeze",
    ]
    assert json.loads(fake.writes()[-1].content)["after"] == "b-c2"


def test_add_checkbox_item_and_missing_heading_is_created():
    notes, fake, _ = _notes()
    notes.add_item_under_heading(CRM, "Commitments", "Pia: send SOW", checkbox=True)
    todo = next(b for b in fake.pages[CRM] if b["type"] == "to_do")
    assert todo["to_do"]["checked"] is False
    notes.add_item_under_heading(CRM, "Next steps", "Book demo")
    assert [b["type"] for b in fake.pages[CRM]][-2:] == ["heading_3", "bulleted_list_item"]


def test_refuses_pages_that_were_not_picked_and_makes_no_request():
    notes, fake, _ = _notes()
    for call in (
        lambda: notes.read_page(OTHER),
        lambda: notes.append_blocks(OTHER, []),
        lambda: notes.edit_block_text(OTHER, "o-1", "x"),
        lambda: notes.add_item_under_heading(OTHER, "Commitments", "x"),
    ):
        with pytest.raises(UnknownPageError):
            call()
    assert fake.requests == []


def test_removing_a_target_revokes_write_access():
    notes, fake, _ = _notes()
    assert notes.state.remove_target(CRM) is True
    with pytest.raises(UnknownPageError):
        notes.add_item_under_heading(CRM, "Commitments", "x")
    assert fake.requests == []


def test_restricted_resource_maps_to_update_content_and_share_the_page():
    notes, fake, _ = _notes()
    fake.restricted = True
    with pytest.raises(ConnectorScopeError) as caught:
        notes.edit_block_text(CRM, "b-c1", "x")
    message = str(caught.value)
    assert caught.value.code == "needs_capabilities" and "Update content" in message and "share the page" in message


# ---- preview, then confirm (HTTP) -------------------------------------------


def test_preview_reads_the_page_and_writes_nothing_until_confirm(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    before = len(fake.writes())
    preview = client.post("/api/notes/notion/update/preview", json={"page_id": CRM, "kind": "commitment", "card_id": "card_1"}).json()
    assert preview["page"] == {"id": CRM, "title": "CRM notes"}
    assert preview["summary"] == "Add to CRM notes under Commitments:"
    assert preview["lines"] == ["- Pia: send SOW by Friday (unspecified)"]
    assert len(fake.writes()) == before and len(fake.pages[CRM]) == 7

    done = client.post("/api/notes/notion/update/confirm", json={"plan_id": preview["plan_id"]}).json()
    assert done["ok"] is True and done["updated"] is True and done["noted_cards"] == ["card_1"]
    assert [_text(b) for b in fake.pages[CRM]][5] == "Pia: send SOW by Friday (unspecified)"
    # A plan can be confirmed once.
    again = client.post("/api/notes/notion/update/confirm", json={"plan_id": preview["plan_id"]})
    assert again.status_code == 400 and again.json()["code"] == "plan_expired"


def test_cancel_discards_the_plan_and_summary_preview_appends(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    preview = client.post("/api/notes/notion/update/preview", json={"page_id": CRM, "kind": "summary"}).json()
    assert preview["summary"].startswith("Add a dated recap to the end of CRM notes")
    assert preview["lines"][1:3] == ["Summary", "- Phase 2 waits for KYC."]
    assert client.post("/api/notes/notion/update/cancel", json={"plan_id": preview["plan_id"]}).json() == {"ok": True}
    assert client.post("/api/notes/notion/update/confirm", json={"plan_id": preview["plan_id"]}).status_code == 400
    assert fake.writes() == []


def test_block_edit_preview_shows_before_and_after(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    preview = client.post("/api/notes/notion/update/preview", json={"page_id": CRM, "kind": "edit", "block_id": "b-r1", "text": "Budget approved"}).json()
    assert preview["lines"] == ["Before: - Budget freeze", "After: Budget approved"]
    client.post("/api/notes/notion/update/confirm", json={"plan_id": preview["plan_id"]})
    assert _text(fake.pages[CRM][6]) == "Budget approved"


def test_preview_refuses_a_page_that_was_not_picked(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    response = client.post("/api/notes/notion/update/preview", json={"page_id": OTHER, "kind": "summary"})
    assert response.status_code == 403 and response.json()["code"] == "unknown_doc"
    assert [r for r in fake.requests if "/children" in r.url.path] == []


def test_targets_show_in_status_and_can_be_removed(tmp_path):
    runtime, client, _fake = _runtime(tmp_path)
    row = client.get("/api/connectors/status").json()["providers"][1]
    assert [t["title"] for t in row["targets"]] == ["CRM notes"] and row["targets"][0]["picked_at"]
    assert client.get("/api/notes/notion").json()["targets"][0]["id"] == CRM
    client.post("/api/connectors/notion/targets", json={"remove": CRM})
    assert client.get("/api/connectors/status").json()["providers"][1]["targets"] == []


def test_restricted_resource_on_confirm_sets_needs_capabilities(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    preview = client.post("/api/notes/notion/update/preview", json={"page_id": CRM, "kind": "summary"}).json()
    fake.restricted = True
    response = client.post("/api/notes/notion/update/confirm", json={"plan_id": preview["plan_id"]})
    assert response.status_code == 403 and response.json()["code"] == "needs_capabilities"
    assert client.get("/api/connectors/status").json()["providers"][1]["needs_capabilities"] is True


# ---- chat: resolve by title, ask, write only after yes -----------------------


def test_chat_update_my_crm_notes_page_asks_then_writes_after_yes(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    ask = client.post("/api/context/chat", json={"message": "update my CRM notes page with this"}).json()
    assert "CRM notes" in ask["reply"] and "Reply yes" in ask["reply"]
    assert fake.writes() == []
    done = client.post("/api/context/chat", json={"message": "yes"}).json()
    assert done["reply"].startswith("Updated CRM notes")
    assert done["changed"][0]["setting"] == "notes_notion"
    assert any(_text(b).startswith("Recap") for b in fake.pages[CRM])


def test_chat_no_cancels_and_unknown_or_unpicked_pages_are_not_touched(tmp_path):
    runtime, client, fake = _runtime(tmp_path)
    client.post("/api/context/chat", json={"message": "update my CRM notes page with this"})
    assert client.post("/api/context/chat", json={"message": "no"}).json()["reply"] == "OK, I won't change the page."
    unknown = client.post("/api/context/chat", json={"message": "update my roadmap page with this"}).json()
    assert "couldn't tell which page" in unknown["reply"] and "CRM notes" in unknown["reply"]
    assert fake.writes() == []


def test_chat_without_any_picked_page_explains_how(tmp_path):
    runtime, client, fake = _runtime(tmp_path, picked=False)
    reply = client.post("/api/context/chat", json={"message": "update my CRM notes page with this"}).json()["reply"]
    assert "Choose a page to keep updated" in reply
    assert fake.writes() == []


# ---- secrets -----------------------------------------------------------------


def test_token_never_leaks_in_responses_or_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    runtime, client, fake = _runtime(tmp_path)
    texts = []
    preview = client.post("/api/notes/notion/update/preview", json={"page_id": CRM, "kind": "summary"})
    texts.append(preview.text)
    texts.append(client.post("/api/notes/notion/update/confirm", json={"plan_id": preview.json()["plan_id"]}).text)
    fake.restricted = True
    texts.append(client.post("/api/notes/notion/update/preview", json={"page_id": OTHER, "kind": "summary"}).text)
    texts += [client.get(path).text for path in ("/api/connectors/status", "/api/status", "/api/notes/notion")]
    for text in texts:
        assert TOKEN not in text
    assert TOKEN not in caplog.text
