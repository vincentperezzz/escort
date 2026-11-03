from pathlib import Path
import pytest
from escort.context.models import CalendarEvent
from escort.context.project import ProjectManager
from escort.context.library import ContextLibrary
from escort.store import MeetingStore


def test_project_manager_init_and_migration(tmp_path: Path):
    base_dir = tmp_path / "desktop"
    base_dir.mkdir(parents=True)
    # Put a mock context.json
    (base_dir / "context.json").write_text('{"items": [], "chat": [{"role": "user", "content": "hi"}]}', encoding="utf-8")

    mgr = ProjectManager(base_dir)
    projects = mgr.list_projects()
    assert len(projects) == 1
    assert projects[0].id == "proj_default"
    assert projects[0].name == "General"
    assert mgr.get_active_project().id == "proj_default"

    # Context file was migrated into proj_default
    migrated = base_dir / "projects" / "proj_default" / "context.json"
    assert migrated.is_file()
    assert '"content": "hi"' in migrated.read_text(encoding="utf-8")


def test_project_crud(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    proj2 = mgr.create_project(
        name="BayaniPay Client",
        description="SOW and integration",
        calendar_keywords=["BayaniPay", "Bayani"],
        calendar_domains=["bayanipay.com"],
        folder_path=str(tmp_path / "notes"),
    )
    assert proj2.name == "BayaniPay Client"
    assert len(mgr.list_projects()) == 2

    # Update project
    updated = mgr.update_project(proj2.id, {"name": "BayaniPay Global", "description": "Updated desc"})
    assert updated.name == "BayaniPay Global"
    assert updated.description == "Updated desc"

    # Set active
    mgr.set_active_project(proj2.id)
    assert mgr.get_active_project().id == proj2.id

    # Switch back and delete
    mgr.set_active_project("proj_default")
    assert mgr.delete_project(proj2.id) is True
    assert len(mgr.list_projects()) == 1


def test_calendar_matching_and_suggestion(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    bayanipay = mgr.create_project(
        name="BayaniPay",
        calendar_keywords=["BayaniPay", "Bayani"],
        calendar_domains=["bayanipay.com"],
    )

    event1 = CalendarEvent(
        id="evt_1",
        title="BayaniPay SOW Weekly Sync",
        start="2026-11-03T10:00:00Z",
        end="2026-11-03T10:30:00Z",
        attendees=["client@bayanipay.com"],
    )

    matched, score = mgr.match_calendar_event(event1)
    assert matched is not None
    assert matched.id == bayanipay.id
    assert score > 0

    # Active project is default, so suggested switch should propose BayaniPay
    suggestion = mgr.suggested_switch([event1])
    assert suggestion is not None
    assert suggestion["project_id"] == bayanipay.id
    assert suggestion["project_name"] == "BayaniPay"

    # Dismiss suggestion
    mgr.dismiss_suggestion(suggestion["suggestion_id"])
    assert mgr.suggested_switch([event1]) is None


def test_sync_local_folder(tmp_path: Path):
    folder = tmp_path / "client_notes"
    folder.mkdir()
    (folder / "sow.md").write_text("# SOW Requirements\nWe require next-day settlement.\n", encoding="utf-8")
    (folder / "extra.txt").write_text("# Extra notes\nEscrow agreement.\n", encoding="utf-8")

    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project(name="Bayani", folder_path=str(folder))

    ctx = ContextLibrary(mgr.get_project_dir(proj.id), lambda k: None)
    synced = mgr.sync_local_folder(proj.id, ctx)
    assert "sow.md" in synced
    assert "extra.txt" in synced
    assert len(ctx.items()) == 2
    titles = [it.title for it in ctx.items()]
    assert "sow" in titles
    assert "extra" in titles

