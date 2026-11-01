from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from escort.whatsapp.service import WhatsAppService


def create_app(service: WhatsAppService, *, poll: bool = False) -> FastAPI:
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if poll:
            threading.Thread(target=_poll_cards, args=(service, stop), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Escort WhatsApp", lifespan=lifespan)
    app.state.service = service

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "channel": "whatsapp"}

    @app.get("/webhook")
    async def verify(request: Request) -> Response:
        params = request.query_params
        challenge = service.verify(
            params.get("hub.mode"),
            params.get("hub.verify_token"),
            params.get("hub.challenge"),
        )
        if challenge is None:
            return Response(status_code=403)
        return PlainTextResponse(challenge)

    @app.post("/webhook")
    async def receive(request: Request) -> Response:
        body = await request.body()
        if not service.signature_ok(body, request.headers.get("x-hub-signature-256")):
            return Response(status_code=403)
        if body:
            service.handle_payload(json.loads(body))
        return {"ok": True}

    return app


def _poll_cards(service: WhatsAppService, stop: threading.Event) -> None:
    seen: set[str] = set()
    primed = False
    while not stop.wait(2):
        cards = service.pull_remote_cards()
        if not primed:
            seen.update(str(card.get("id")) for card in cards if card.get("id"))
            primed = True
            continue
        fresh = [card for card in cards if str(card.get("id")) not in seen]
        seen.update(str(card.get("id")) for card in fresh if card.get("id"))
        if fresh:
            service.fanout(fresh)
