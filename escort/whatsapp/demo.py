from __future__ import annotations

import json
from pathlib import Path

import httpx

from escort.store import MeetingStore
from escort.whatsapp.config import WhatsAppConfig
from escort.whatsapp.graph import GraphClient
from escort.whatsapp.service import WhatsAppService
from escort.whatsapp.signature import sign_body


DEMO_NUMBER = "15550001111"
DEMO_SECRET = "demo-secret"


def demo_config(tmp: Path | None = None) -> WhatsAppConfig:
    del tmp
    return WhatsAppConfig(
        verify_token="demo-verify",
        app_secret=DEMO_SECRET,
        allowed_numbers=(DEMO_NUMBER,),
        access_token="demo-access",
        phone_number_id="PHONE_ID",
        graph_version="v22.0",
        state_url=None,
        model_api_key=None,
    )


def build_demo_service(media_dir: Path, graph_http: httpx.Client) -> WhatsAppService:
    config = demo_config()
    return WhatsAppService(config, GraphClient(config, graph_http), MeetingStore(media_dir))


def scripted_payloads() -> list[dict]:
    return [
        text_payload(DEMO_NUMBER, "start meeting"),
        document_payload(DEMO_NUMBER, "prd-1"),
        text_payload(DEMO_NUMBER, "Is payment integration in Phase 1?"),
        text_payload("19998887777", "start meeting"),
        action_payload(DEMO_NUMBER, "note|card_conflict", "Note it"),
        text_payload(DEMO_NUMBER, "end meeting"),
    ]


def text_payload(sender: str, body: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": sender, "type": "text", "text": {"body": body}},
                            ]
                        }
                    }
                ]
            }
        ],
    }


def document_payload(sender: str, media_id: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": sender,
                                    "type": "document",
                                    "document": {
                                        "id": media_id,
                                        "filename": "bayanipay.md",
                                        "mime_type": "text/markdown",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def action_payload(sender: str, action_id: str, title: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": sender,
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": action_id, "title": title},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def sample_cards() -> list[dict]:
    return [
        {
            "id": "card_conflict",
            "type": "PRD_CONFLICT",
            "title": "Payment integration is not Phase 1",
            "body": "They want it pulled into Phase 1. The PRD says Phase 2.",
            "prd_section": "2.2 Phase 2 — Payment integration",
            "latency_ms": 3.1,
        },
        {
            "id": "card_say_a",
            "type": "SUGGESTED_REPLY",
            "title": "Park it",
            "body": "Payment integration is Phase 2 pa.",
            "prd_section": "2.2 Phase 2 — Payment integration",
            "latency_ms": 3.2,
        },
        {
            "id": "card_say_b",
            "type": "SUGGESTED_REPLY",
            "title": "Ask finance",
            "body": "Yung 2.9 percent, finance muna.",
            "prd_section": "3. Pricing",
            "latency_ms": 3.4,
        },
    ]


def print_demo(media_dir: Path) -> list[dict]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages") or request.url.path.endswith("/media"):
            body = json.loads(request.content.decode() or "{}") if request.headers.get("content-type", "").startswith("application/json") else {"upload": request.url.path}
            sent.append({"url": str(request.url), "body": body})
            if request.url.path.endswith("/media") and not request.url.path.endswith("/messages"):
                return httpx.Response(200, json={"id": "media-1"})
            return httpx.Response(200, json={"messages": [{"id": "wamid"}]})
        if "lookaside" in request.url.host:
            return httpx.Response(
                200,
                content="# Scope\n\n### 2.2 Phase 2 — Payment integration\nPayment integration ships in Phase 2.\n".encode(),
            )
        if request.url.path.endswith("/prd-1"):
            return httpx.Response(200, json={"url": "https://lookaside.example/prd-1"})
        return httpx.Response(404)

    config = demo_config()
    graph = GraphClient(config, httpx.Client(transport=httpx.MockTransport(handler)))
    service = WhatsAppService(config, graph, MeetingStore(media_dir))
    service.handle_payload(text_payload(DEMO_NUMBER, "start meeting"))
    service.handle_payload(document_payload(DEMO_NUMBER, "prd-1"))
    service.fanout(sample_cards())
    service.handle_payload(action_payload(DEMO_NUMBER, "note|card_conflict", "Note it"))
    service.handle_payload(text_payload("19998887777", "start meeting"))
    service.handle_payload(text_payload(DEMO_NUMBER, "end meeting"))
    for item in sent:
        body = item["body"]
        kind = body.get("type") or "upload"
        preview = ""
        if kind == "text":
            preview = body.get("text", {}).get("body", "")[:80]
        elif kind == "interactive":
            preview = body.get("interactive", {}).get("type", "")
        print(f"{kind}: {preview}")
    return sent
