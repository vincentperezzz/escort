from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from escort.context.sources.base import ConnectorError
from escort.desktop.runtime import DesktopRuntime

PROVIDERS = ("google", "notion")


def create_desktop_app(runtime: DesktopRuntime, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Escort")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runtime = runtime

    @app.exception_handler(ConnectorError)
    async def connector_error(_request: Request, err: ConnectorError) -> JSONResponse:
        return JSONResponse(status_code=err.status_code, content={"error": str(err), "provider": err.provider})

    @app.get("/api/status")
    def status() -> dict:
        return runtime.public_status()

    @app.post("/api/setup")
    def setup(body: dict) -> dict:
        return runtime.save_setup(body)

    @app.post("/api/prd")
    @app.post("/api/notes")
    def prd(body: dict) -> dict:
        return runtime.set_prd(str(body.get("text") or ""), str(body.get("name") or "uploaded.md"))

    @app.post("/api/demo")
    def demo() -> dict:
        return runtime.load_demo()

    @app.post("/api/listen")
    def listen(body: dict) -> dict:
        return runtime.set_listening(bool(body.get("on")))

    @app.post("/api/cards/{card_id}/action")
    def action(card_id: str, body: dict) -> dict:
        return runtime.act(card_id, str(body.get("action") or ""))

    @app.post("/api/meeting/delete")
    def delete_meeting() -> dict:
        return runtime.delete_meeting()

    @app.post("/api/context/upload")
    async def context_upload(file: UploadFile = File(...)) -> dict:
        data = await file.read()
        name = file.filename or "upload.txt"
        return runtime.context_upload(name, data)

    @app.delete("/api/context/items/{item_id}")
    def context_remove(item_id: str) -> dict:
        return runtime.context_remove(item_id)

    @app.post("/api/context/import")
    def context_import(body: dict) -> dict:
        return runtime.context_import(str(body.get("source") or ""), str(body.get("external_id") or ""))

    @app.post("/api/context/search")
    def context_search(body: dict) -> dict:
        return runtime.context_search(str(body.get("source") or ""), str(body.get("query") or ""))

    @app.post("/api/context/match")
    def context_match(body: dict) -> dict:
        return runtime.context_match(str(body.get("event_id") or ""))

    @app.post("/api/context/prep")
    def context_prep(body: dict) -> dict:
        ids = body.get("item_ids") or []
        return runtime.context_prep(str(body.get("event_id") or ""), [str(item) for item in ids])

    @app.post("/api/context/chat")
    def context_chat(body: dict) -> dict:
        return runtime.context_chat(str(body.get("message") or ""))

    @app.get("/api/connectors/status")
    def connectors_status() -> dict:
        return runtime.connectors_status()

    @app.post("/api/connectors/google/connect")
    def connect_google() -> dict:
        return runtime.connect_google()

    @app.post("/api/connectors/notion/connect")
    def connect_notion(body: dict) -> dict:
        return runtime.connect_notion(str(body.get("token") or ""))

    @app.post("/api/connectors/{provider}/disconnect", response_model=None)
    def disconnect(provider: str) -> dict | JSONResponse:
        if provider not in PROVIDERS:
            return JSONResponse(status_code=404, content={"error": f"Unknown connector: {provider}"})
        return runtime.disconnect_connector(provider)

    @app.post("/api/connectors/token")
    def connector_token(body: dict) -> dict:
        """Compat: store a raw token. Prefer /api/connectors/{google,notion}/connect."""
        name = str(body.get("name") or "")
        token = str(body.get("token") or "").strip()
        if name in PROVIDERS and token:
            runtime.secrets.set(f"{name}_oauth_token", token)
            runtime.connectors.google.on_change()
        return runtime.public_status()

    if ui_dir and (ui_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=ui_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            target = ui_dir / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(ui_dir / "index.html")

    return app
