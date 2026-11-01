import io
import json
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from escort.store import MeetingStore
from escort.whatsapp.app import create_app
from escort.whatsapp.config import WhatsAppConfig
from escort.whatsapp.demo import (
    DEMO_NUMBER,
    DEMO_SECRET,
    action_payload,
    demo_config,
    document_payload,
    print_demo,
    sample_cards,
    text_payload,
)
from escort.whatsapp.extract import extract_text
from escort.whatsapp.graph import GraphClient
from escort.whatsapp.service import WhatsAppService
from escort.whatsapp.signature import sign_body


def _config(**overrides) -> WhatsAppConfig:
    base = demo_config()
    data = {
        "verify_token": base.verify_token,
        "app_secret": base.app_secret,
        "allowed_numbers": base.allowed_numbers,
        "access_token": base.access_token,
        "phone_number_id": base.phone_number_id,
        "graph_version": base.graph_version,
        "state_url": None,
        "model_api_key": None,
        "model_base": base.model_base,
        "spark_model": base.spark_model,
    }
    data.update(overrides)
    return WhatsAppConfig(**data)


def _graph(config: WhatsAppConfig, sent: list[dict], files: dict[str, bytes] | None = None) -> GraphClient:
    library = files or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages"):
            sent.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={"messages": [{"id": "wamid"}]})
        if path.endswith("/media"):
            sent.append({"upload": path})
            return httpx.Response(200, json={"id": "media-1"})
        media_id = path.rsplit("/", 1)[-1]
        if media_id in library:
            return httpx.Response(200, json={"url": f"https://lookaside.example/{media_id}"})
        if request.url.host == "lookaside.example":
            return httpx.Response(200, content=library[media_id])
        return httpx.Response(404)

    return GraphClient(config, httpx.Client(transport=httpx.MockTransport(handler)))


def _service(tmp_path: Path, sent: list[dict], **overrides) -> WhatsAppService:
    config = _config(**overrides)
    files = overrides.pop("_files", None)
    return WhatsAppService(config, _graph(config, sent, files), MeetingStore(tmp_path))


def test_verify_handshake_and_signature(tmp_path: Path):
    sent: list[dict] = []
    service = _service(tmp_path, sent)
    app = create_app(service)
    client = TestClient(app)
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "demo-verify", "hub.challenge": "abc123"})
    assert ok.status_code == 200
    assert ok.text == "abc123"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "abc"}).status_code == 403
    body = json.dumps(text_payload(DEMO_NUMBER, "start meeting")).encode()
    denied = client.post("/webhook", content=body, headers={"content-type": "application/json"})
    assert denied.status_code == 403
    signed = client.post(
        "/webhook",
        content=body,
        headers={"content-type": "application/json", "x-hub-signature-256": sign_body(DEMO_SECRET, body)},
    )
    assert signed.status_code == 200
    assert any(item.get("type") == "text" and "24-hour" in item["text"]["body"] for item in sent)


def test_allowlist_blocks_strangers_and_questions_hit_muse(tmp_path: Path):
    sent: list[dict] = []
    asked: list[dict] = []

    def muse(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Phase 2 pa yan."}}]})

    config = _config(model_api_key="LLM_test")
    service = WhatsAppService(
        config,
        _graph(config, sent),
        MeetingStore(tmp_path),
        model_http=httpx.Client(transport=httpx.MockTransport(muse)),
    )
    service.handle_payload(text_payload("19998887777", "start meeting"))
    assert sent == []
    service.handle_payload(text_payload(DEMO_NUMBER, "start meeting"))
    service.handle_payload(text_payload(DEMO_NUMBER, "Is payment integration in Phase 1?"))
    assert asked
    assert asked[0]["model"] == "muse-spark-1.3"
    assert asked[0]["reasoning_effort"] == "minimal"
    assert asked[0]["max_completion_tokens"] >= 1500
    assert any(item.get("text", {}).get("body") == "Phase 2 pa yan." for item in sent)


def test_cards_queue_outside_the_window_then_push_with_buttons(tmp_path: Path):
    sent: list[dict] = []
    config = _config(app_secret=None)
    crop = tmp_path / "crop_fee.png"
    crop.write_bytes(b"\x89PNG\r\n")
    service = WhatsAppService(config, _graph(config, sent), MeetingStore(tmp_path))
    cards = sample_cards()
    cards[0]["crop_url"] = f"/panel/media/{crop.name}"
    service.fanout(cards)
    assert sent == []
    service.handle_payload(text_payload(DEMO_NUMBER, "start meeting"))
    kinds = [item.get("type") for item in sent]
    assert "image" in kinds
    assert "interactive" in kinds
    buttons = [item for item in sent if item.get("interactive", {}).get("type") == "button"]
    lists = [item for item in sent if item.get("interactive", {}).get("type") == "list"]
    assert buttons
    titles = [button["reply"]["title"] for button in buttons[0]["interactive"]["action"]["buttons"]]
    assert titles == ["Push back", "Note it", "Ignore"]
    assert lists
    assert len(lists[0]["interactive"]["action"]["sections"][0]["rows"]) == 2
    assert sent[0]["to"] == DEMO_NUMBER
    assert "graph.facebook.com/v22.0/PHONE_ID/messages" in service.graph.root + "/PHONE_ID/messages"


def test_note_it_adds_a_commitment_and_document_sets_prd(tmp_path: Path):
    sent: list[dict] = []
    prd = "# Scope\n\n### 2.2 Phase 2 — Payment integration\nPayment integration ships in Phase 2.\n".encode()
    config = _config(app_secret=None)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages"):
            sent.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={"messages": [{"id": "wamid"}]})
        if request.url.host == "lookaside.example":
            return httpx.Response(200, content=prd)
        if path.endswith("/prd-1"):
            return httpx.Response(200, json={"url": "https://lookaside.example/prd-1"})
        return httpx.Response(404)

    service = WhatsAppService(config, GraphClient(config, httpx.Client(transport=httpx.MockTransport(handler))), MeetingStore(tmp_path))
    service.handle_payload(text_payload(DEMO_NUMBER, "start meeting"))
    service.handle_payload(document_payload(DEMO_NUMBER, "prd-1"))
    assert service.sessions[DEMO_NUMBER].prd_text.startswith("# Scope")
    assert service.store.prd_sections
    service.fanout(sample_cards())
    service.handle_payload(action_payload(DEMO_NUMBER, "note|card_conflict", "Note it"))
    commitments = service.store.meeting()["commitments"]
    assert commitments
    assert commitments[-1]["who"] == "Me"
    assert commitments[-1]["when"] == "this meeting"
    service.handle_payload(text_payload(DEMO_NUMBER, "end meeting"))
    ending = [item["text"]["body"] for item in sent if item.get("type") == "text"][-1]
    assert "Commitments" in ending
    assert "24-hour" in ending


def test_graph_version_is_configurable(tmp_path: Path):
    sent: list[dict] = []
    config = _config(app_secret=None, graph_version="v21.0")
    service = WhatsAppService(config, _graph(config, sent), MeetingStore(tmp_path))
    service.handle_payload(text_payload(DEMO_NUMBER, "start meeting"))
    assert service.graph.root == "https://graph.facebook.com/v21.0"


def test_extract_md_docx_and_pdf():
    assert "Phase 2" in extract_text("scope.md", b"Phase 2 only")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:p><w:t>KYC stays in Phase 1</w:t></w:p>")
    assert "KYC stays in Phase 1" in extract_text("prd.docx", buffer.getvalue())
    pdf = b"BT (GCash is Phase 2.) Tj ET"
    assert "GCash is Phase 2." in extract_text("fee.pdf", pdf)


def test_remote_state_cards_can_be_pulled(tmp_path: Path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cards": [{"id": "card_remote", "type": "COMMITMENT", "title": "Check", "body": "check"}]})

    config = _config(app_secret=None, state_url="http://escort.local/panel/state")
    service = WhatsAppService(
        config,
        _graph(config, []),
        MeetingStore(tmp_path),
        state_http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    cards = service.pull_remote_cards()
    assert cards[0]["id"] == "card_remote"


def test_fake_webhook_replay_runs_without_a_real_number(tmp_path: Path):
    sent = print_demo(tmp_path)
    bodies = [item["body"].get("text", {}).get("body", "") for item in sent if item["body"].get("type") == "text"]
    assert any("24-hour" in body for body in bodies)
    assert any(item["body"].get("interactive", {}).get("type") == "button" for item in sent)
    assert any(item["body"].get("interactive", {}).get("type") == "list" for item in sent)
    assert not any(item["body"].get("to") == "19998887777" for item in sent)
