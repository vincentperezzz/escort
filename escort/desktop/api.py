from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from escort.desktop.runtime import DesktopRuntime


def create_desktop_app(runtime: DesktopRuntime, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Escort")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runtime = runtime

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

    if ui_dir and (ui_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=ui_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            target = ui_dir / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(ui_dir / "index.html")

    return app
