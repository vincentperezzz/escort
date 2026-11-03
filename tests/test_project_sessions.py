from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from escort.context.library import ContextLibrary
from escort.context.project import ProjectManager
from escort.context.sessions import ProjectSessionStore, migrate_legacy_recaps
from escort.context.sources.registry import folder_watch_active, register_folder_watch, stop_folder_watch
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore, utc_now


def _client(tmp_path: Path) -> tuple[TestClient, DesktopRuntime]:
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")
    return TestClient(create_desktop_app(runtime)), runtime


def _seed_session(project_dir: Path, project_id: str, title: str) -> str:
    store = ProjectSessionStore(project_dir)
    record = store.save(
        project_id=project_id,
        title=title,
        started_at=utc_now(),
        ended_at=utc_now(),
        summary={"status": "ready", "title": title, "bullets": [f"Bullet for {title}"], "commitments": [], "conflicts": []},
        snapshot={"utterances": [{"speaker": "Me", "text": "Hello", "created_at": utc_now()}], "cards": [], "meeting": {}},
    )
    return str(record["id"])


def test_sessions_list_isolated_per_project(tmp_path: Path):
    client, runtime = _client(tmp_path)
    created = client.post("/api/projects", json={"name": "Alpha", "activate": True}).json()
    alpha_id = created["active_project"]["id"]
    beta = client.post("/api/projects", json={"name": "Beta", "activate": False}).json()
    beta_id = next(p["id"] for p in beta["projects"] if p["name"] == "Beta")
    alpha_dir = runtime.projects.get_project_dir(alpha_id)
    beta_dir = runtime.projects.get_project_dir(beta_id)
    alpha_sess = _seed_session(alpha_dir, alpha_id, "Alpha call")
    _seed_session(beta_dir, beta_id, "Beta call")

    alpha_list = client.get(f"/api/sessions?project_id={alpha_id}").json()
    assert len(alpha_list["sessions"]) == 1
    assert alpha_list["sessions"][0]["id"] == alpha_sess
    assert alpha_list["sessions"][0]["project_id"] == alpha_id

    beta_list = client.get(f"/api/sessions?project_id={beta_id}").json()
    assert len(beta_list["sessions"]) == 1
    assert beta_list["sessions"][0]["title"] == "Beta call"

    recap = client.get(f"/api/sessions/{alpha_sess}/recap").json()
    assert recap["id"] == alpha_sess
    assert recap["title"] == "Alpha call"
    assert isinstance(recap["transcript"], list)
    assert recap["transcript"][0]["speaker"] == "Me"

    deleted = client.delete(f"/api/sessions/{alpha_sess}").json()
    assert deleted == {"ok": True}
    assert client.get(f"/api/sessions/{alpha_sess}/recap").status_code == 404


def test_legacy_last_recap_migration(tmp_path: Path):
    base = tmp_path / "desktop"
    base.mkdir()
    (base / "context.json").write_text('{"items": [], "chat": []}', encoding="utf-8")
    legacy = {
        "title": "Legacy meeting",
        "started_at": utc_now(),
        "summary": {"status": "ready", "title": "Legacy meeting", "bullets": ["Imported"], "commitments": [], "conflicts": []},
        "transcript": [{"t": utc_now(), "speaker": "Them", "text": "Hi"}],
    }
    (base / "last_recap.json").write_text(json.dumps(legacy), encoding="utf-8")
    mgr = ProjectManager(base)
    default = mgr.get_active_project()
    assert default is not None
    rows = mgr.list_sessions(default.id)
    assert len(rows) == 1
    assert rows[0]["title"] == "Legacy meeting"
    assert not (base / "last_recap.json").exists()


def test_delete_project_cascade(tmp_path: Path):
    client, runtime = _client(tmp_path)
    created = client.post("/api/projects", json={"name": "Cascade", "activate": True}).json()
    pid = created["active_project"]["id"]
    proj_dir = runtime.projects.get_project_dir(pid)
    ctx = ContextLibrary(proj_dir, lambda _k: None)
    ctx.add_paste("hello", title="note")
    uploads = proj_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "file.txt").write_text("x", encoding="utf-8")
    _seed_session(proj_dir, pid, "One")
    runtime.prefs.setdefault("project_destinations", {})[pid] = {"notes_destination": "notion"}
    runtime._write_prefs()
    register_folder_watch(pid, str(tmp_path / "watch-me"))

    res = client.delete(f"/api/projects/{pid}").json()
    assert res["ok"] is True
    assert res["deleted"]["files"] >= 1
    assert res["deleted"]["items"] >= 1
    assert res["deleted"]["sessions"] == 1
    assert res["deleted"]["chat_messages"] >= 0
    assert not proj_dir.exists()
    assert stop_folder_watch(pid) is False
    assert folder_watch_active(pid) is False
    assert pid not in (runtime.prefs.get("project_destinations") or {})


def test_delete_last_project_sets_needs_project(tmp_path: Path):
    client, _runtime = _client(tmp_path)
    created = client.post("/api/projects", json={"name": "Only", "activate": True}).json()
    pid = created["active_project"]["id"]
    deleted = client.delete(f"/api/projects/{pid}").json()
    assert deleted["ok"] is True
    status = client.get("/api/status").json()
    assert status["needs_project"] is True
    assert status["active_project"] is None


def test_edit_project_validation(tmp_path: Path):
    client, _runtime = _client(tmp_path)
    first = client.post("/api/projects", json={"name": "One", "activate": True}).json()
    pid = first["active_project"]["id"]
    client.post("/api/projects", json={"name": "Two", "activate": False})

    updated = client.put(f"/api/projects/{pid}", json={"name": "Renamed", "description": ""}).json()
    assert updated["project"]["name"] == "Renamed"
    assert updated["project"]["description"] == ""

    dup = client.put(f"/api/projects/{pid}", json={"name": "Two"})
    assert dup.status_code == 400
    assert "already exists" in dup.json()["error"].lower()

    blank = client.put(f"/api/projects/{pid}", json={"name": "   "})
    assert blank.status_code == 400

    missing = client.put("/api/projects/proj_nope", json={"name": "X"})
    assert missing.status_code == 404

    traversal = client.delete("/api/projects/../secrets")
    assert traversal.status_code == 404
