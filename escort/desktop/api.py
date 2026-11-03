from __future__ import annotations

import re
from pathlib import Path

from fastapi import Body, FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from escort.context.sources.base import ConnectorError
from escort.desktop.runtime import DesktopRuntime

PROVIDERS = ("google", "notion")
LOCAL_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")
# "testclient" is the host Starlette's TestClient reports; a real remote peer is always an IP.
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host not in LOOPBACK_HOSTS:
        return False
    origin = request.headers.get("origin")
    return origin is None or bool(LOCAL_ORIGIN.match(origin))


def create_desktop_app(runtime: DesktopRuntime, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Escort")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=LOCAL_ORIGIN.pattern,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runtime = runtime

    @app.exception_handler(ConnectorError)
    async def connector_error(_request: Request, err: ConnectorError) -> JSONResponse:
        return JSONResponse(status_code=err.status_code, content=err.payload())

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

    @app.get("/api/overlay")
    def overlay() -> dict:
        return runtime.overlay_status()

    @app.post("/api/overlay", response_model=None)
    def set_overlay(body: dict) -> dict | JSONResponse:
        def flag(name: str) -> bool | None:
            value = body.get(name)
            return None if value is None else bool(value)

        opacity = body.get("opacity")
        if opacity is not None:
            try:
                opacity = float(opacity)
            except (TypeError, ValueError):
                return JSONResponse(status_code=422, content={"error": "opacity must be a number between 0.3 and 1.0"})
        return runtime.set_overlay(
            hide_from_share=flag("hide_from_share"),
            visible=flag("visible"),
            collapsed=flag("collapsed"),
            opacity=opacity,
            input_focus=flag("input_focus"),
        )

    @app.post("/api/main-window/show")
    def show_main_window() -> dict:
        runtime.show_main_window()
        return {"ok": True}

    @app.get("/api/monitors")
    def monitors() -> dict:
        return runtime.monitors_status(fresh=True)

    @app.post("/api/monitors", response_model=None)
    def set_monitor(body: dict) -> dict | JSONResponse:
        if "selected" not in body:
            return JSONResponse(status_code=422, content={"error": "selected is required: 'primary', 'all' or a display number"})
        try:
            return runtime.set_watch_monitor(body["selected"])
        except ValueError as err:
            return JSONResponse(status_code=422, content={"error": str(err)})

    @app.get("/api/summary")
    def summary() -> dict:
        return runtime.summary_status()

    @app.post("/api/summary/regenerate")
    def regenerate_summary() -> dict:
        return runtime.regenerate_summary()

    @app.get("/api/sessions", response_model=None)
    def list_sessions(project_id: str | None = None) -> dict | JSONResponse:
        try:
            return runtime.list_sessions(project_id)
        except KeyError as err:
            return JSONResponse(status_code=404, content={"error": str(err)})

    @app.get("/api/sessions/{session_id}/recap", response_model=None)
    def session_recap(session_id: str) -> dict | JSONResponse:
        try:
            return runtime.session_recap(session_id)
        except KeyError as err:
            return JSONResponse(status_code=404, content={"error": str(err)})

    @app.delete("/api/sessions/{session_id}", response_model=None)
    def delete_session(session_id: str) -> dict | JSONResponse:
        try:
            return runtime.delete_session(session_id)
        except KeyError as err:
            return JSONResponse(status_code=404, content={"error": str(err)})

    @app.get("/api/transcript")
    def transcript() -> dict:
        return runtime.transcript()

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

    @app.post("/api/connectors/google/notes")
    def google_notes(body: dict) -> dict:
        """Turn 'Save notes to Drive' on/off; on without the scopes returns an auth_url."""
        return runtime.set_save_notes(bool(body.get("enabled")))

    @app.post("/api/connectors/google/consent")
    def google_consent(body: dict | None = Body(default=None)) -> dict:
        """Incremental consent for one capability: {capability: notes (default) | docs | sheets | slides | drive_files}."""
        capability = str((body or {}).get("capability") or "notes")
        return runtime.google_notes_consent(capability)

    @app.get("/api/google/picker-config", response_model=None)
    def google_picker_config(request: Request) -> dict | JSONResponse:
        """API key, App ID and a short-lived access token for Google's Drive file chooser.

        Only for the local UI: refused for non-loopback callers and foreign browser origins.
        Never includes the OAuth client secret.
        """
        if not _local_request(request):
            return JSONResponse(status_code=403, content={"error": "The Drive file chooser config is only available to the local Escort window."})
        return JSONResponse(content=runtime.google_picker_config(), headers={"Cache-Control": "no-store"})

    @app.post("/api/context/import-drive-file")
    def context_import_drive_file(body: dict) -> dict:
        return runtime.context_import_drive_file(
            str(body.get("id") or body.get("file_id") or ""),
            str(body.get("mimeType") or body.get("mime_type") or ""),
            str(body.get("name") or ""),
        )

    @app.get("/api/notes/drive")
    def notes_drive() -> dict:
        return runtime.notes_drive_status()

    @app.post("/api/notes/drive")
    def save_notes_drive(body: dict) -> dict:
        card_id = body.get("card_id")
        return runtime.save_notes_drive(str(body.get("kind") or ""), str(card_id) if card_id else None)

    @app.get("/api/notes/notion")
    def notes_notion() -> dict:
        return runtime.notes_notion_status()

    @app.post("/api/notes/notion")
    def save_notes_notion(body: dict) -> dict:
        card_id = body.get("card_id")
        return runtime.save_notes_notion(str(body.get("kind") or ""), str(card_id) if card_id else None)

    @app.get("/api/notes/destinations")
    def notes_destinations() -> dict:
        return runtime.notes_status()

    @app.post("/api/connectors/notion/notes")
    def notion_notes(body: dict) -> dict:
        """Turn 'Save notes to Notion' on/off; parent_id picks the page Escort writes under."""
        parent_id = body.get("parent_id")
        return runtime.set_notion_notes(
            bool(body.get("enabled")),
            str(parent_id) if parent_id else None,
            str(body.get("parent_title") or "") or None,
        )

    @app.post("/api/connectors/notion/targets")
    def notion_targets(body: dict) -> dict:
        """'Choose a page to keep updated': {add: {id, title}} or {remove: id}."""
        if body.get("remove"):
            return runtime.remove_notion_target(str(body["remove"]))
        added = body.get("add") or {}
        return runtime.add_notion_target(str(added.get("id") or ""), str(added.get("title") or "") or None)

    @app.post("/api/notes/notion/update/preview")
    def notion_update_preview(body: dict) -> dict:
        """What 'Update an existing page' would write, read from the page first. Writes nothing."""
        return runtime.notion_update_preview(
            str(body.get("page_id") or ""),
            str(body.get("kind") or ""),
            str(body["card_id"]) if body.get("card_id") else None,
            str(body["block_id"]) if body.get("block_id") else None,
            str(body["text"]) if body.get("text") else None,
        )

    @app.post("/api/notes/notion/update/confirm")
    def notion_update_confirm(body: dict) -> dict:
        return runtime.notion_update_confirm(str(body.get("plan_id") or ""))

    @app.post("/api/notes/notion/update/cancel")
    def notion_update_cancel(body: dict) -> dict:
        return runtime.notion_update_cancel(str(body.get("plan_id") or ""))

    @app.post("/api/connectors/notion/connect")
    def connect_notion(body: dict | None = Body(default=None)) -> dict:
        """No body: one-click OAuth, returns {auth_url} (poll /api/connectors/status). {token}: Advanced token fallback."""
        return runtime.connect_notion(str((body or {}).get("token") or ""))

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

    # --- Projects / Topics -----------------------------------------------------------
    @app.get("/api/projects")
    def list_projects() -> dict:
        active = runtime.projects.get_active_project()
        return {
            "projects": [p.to_dict() for p in runtime.projects.list_projects()],
            "active_project": active.to_dict() if active else None,
        }

    @app.get("/api/projects/{project_id}/meetings")
    def get_project_meetings(project_id: str) -> dict:
        return runtime.get_project_meetings(project_id)

    @app.put("/api/projects/{project_id}/meetings")
    def put_project_meetings(project_id: str, body: dict) -> dict:
        return runtime.put_project_meetings(project_id, body)

    @app.post("/api/projects/{project_id}/meetings/rescan")
    def rescan_project_meetings(project_id: str) -> dict:
        return runtime.rescan_project_meetings(project_id)

    @app.get("/api/calendar/events")
    def calendar_events() -> dict:
        return runtime.calendar_events_public()

    @app.post("/api/projects")
    def create_project(body: dict) -> dict:
        return runtime.create_project(body)

    @app.put("/api/projects/{project_id}", response_model=None)
    def update_project(project_id: str, body: dict) -> dict | JSONResponse:
        try:
            return runtime.update_project(project_id, body)
        except KeyError as err:
            return JSONResponse(status_code=404, content={"error": str(err)})
        except ValueError as err:
            return JSONResponse(status_code=400, content={"error": str(err)})

    @app.delete("/api/projects/{project_id}", response_model=None)
    def delete_project(project_id: str) -> dict | JSONResponse:
        try:
            return runtime.delete_project(project_id)
        except KeyError as err:
            return JSONResponse(status_code=404, content={"error": str(err)})

    @app.post("/api/projects/{project_id}/activate")
    def activate_project(project_id: str) -> dict:
        return runtime.set_active_project(project_id)

    @app.post("/api/projects/{project_id}/sync-folder")
    def sync_project_folder(project_id: str) -> dict:
        return runtime.sync_project_folder(project_id)

    @app.post("/api/projects/dismiss-suggestion")
    def dismiss_suggestion(body: dict) -> dict:
        return runtime.dismiss_project_suggestion(str(body.get("suggestion_id") or ""))

    @app.post("/api/projects/reset")
    def reset_projects(body: dict) -> dict:
        return runtime.reset_projects(str(body.get("name") or "General"), str(body.get("description") or ""))

    @app.post("/api/context/clear-chat")
    def clear_chat() -> dict:
        return runtime.clear_chat()

    if ui_dir and (ui_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=ui_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            target = ui_dir / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(ui_dir / "index.html")

    return app
