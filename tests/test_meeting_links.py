from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from escort.context.mock_data import mock_calendar
from escort.context.models import CalendarEvent
from escort.context.project import ProjectManager
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from dataclasses import replace

from escort.settings import load_settings
from escort.store import MeetingStore


def _runtime(tmp_path: Path) -> DesktopRuntime:
    return DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")


def _client(tmp_path: Path) -> tuple[TestClient, DesktopRuntime]:
    runtime = _runtime(tmp_path)
    return TestClient(create_desktop_app(runtime)), runtime


def test_fresh_install_needs_project(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    assert mgr.list_projects() == []
    assert mgr.needs_project() is True
    assert mgr.get_active_project() is None


def test_ai_linking_and_user_exclusion(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    bayani = mgr.create_project(name="BayaniPay", description="Demo and pipeline reviews")
    mgr.create_project(name="Robotics Lab", description="Hackathon planning")
    events = mock_calendar()
    settings = replace(load_settings(), api_key=None)
    mgr.sync_meeting_links(events, settings)
    bayani_view = mgr.meetings_view(bayani.id)
    linked_ids = {row["event_id"] for row in bayani_view["meetings"]}
    assert "evt_bayani_scope" in linked_ids
    assert "evt_weekly_sync" not in linked_ids
    mgr.update_meetings(bayani.id, [], ["evt_bayani_scope"], events)
    resync = mgr.meetings_view(bayani.id)
    assert resync["meetings"] == []
    mgr.sync_meeting_links(events, settings)
    assert "evt_bayani_scope" not in {row["event_id"] for row in mgr.meetings_view(bayani.id)["meetings"]}


def test_user_link_persists_over_rescan(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project(name="Weekly Sync", description="Internal standups")
    events = mock_calendar()
    mgr.update_meetings(proj.id, ["evt_weekly_sync"], [], events)
    view = mgr.meetings_view(proj.id)
    assert len(view["meetings"]) == 1
    assert view["meetings"][0]["linked_by"] == "user"
    settings = replace(load_settings(), api_key=None)
    mgr.rescan_meeting_links(proj.id, events, settings)
    again = mgr.meetings_view(proj.id)
    assert len(again["meetings"]) == 1
    assert again["meetings"][0]["linked_by"] == "user"


def test_scoped_upcoming_in_status(tmp_path: Path, monkeypatch):
    client, runtime = _client(tmp_path)
    bayani = runtime.projects.create_project(name="BayaniPay", description="Scope and SOW")
    runtime.projects.create_project(name="Other", description="Unrelated")
    runtime.projects.set_active_project(bayani.id)
    runtime.context.switch_root(runtime.projects.get_active_project_dir())
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    events = mock_calendar() + [
        CalendarEvent(
            id="evt_future_demo",
            title="BayaniPay Demo",
            start=soon,
            end=soon,
            attendees=["boss@example.com"],
            mock=True,
        )
    ]
    runtime.projects.sync_meeting_links(events, replace(load_settings(), api_key=None))
    runtime.projects.update_meetings(bayani.id, ["evt_future_demo"], [], events)
    monkeypatch.setattr(runtime.context, "upcoming_events", lambda limit=25: events[:limit])
    status = client.get("/api/status").json()
    upcoming_titles = [row["title"] for row in status["context"]["upcoming"]]
    assert any("BayaniPay" in title for title in upcoming_titles)
    assert not any("Weekly product sync" in title for title in upcoming_titles)
    assert status["needs_project"] is False
    assert status["active_project_next_meeting"] is not None


def test_meeting_api_shapes(tmp_path: Path):
    client, runtime = _client(tmp_path)
    proj = runtime.projects.create_project(name="BayaniPay", description="Demos")
    runtime.projects.set_active_project(proj.id)
    events = mock_calendar()
    runtime.projects.sync_meeting_links(events, replace(load_settings(), api_key=None))
    meetings = client.get(f"/api/projects/{proj.id}/meetings").json()
    assert "meetings" in meetings and "next" in meetings
    assert meetings["meetings"] and {"event_id", "title", "start", "end", "attendees", "linked_by"} <= set(meetings["meetings"][0])
    cal = client.get("/api/calendar/events").json()
    assert "events" in cal
    assert {"event_id", "title", "start", "end", "attendees", "project_id"} <= set(cal["events"][0])
    updated = client.put(
        f"/api/projects/{proj.id}/meetings",
        json={"add": ["evt_weekly_sync"], "remove": []},
    ).json()
    assert any(row["event_id"] == "evt_weekly_sync" and row["linked_by"] == "user" for row in updated["meetings"])
    rescan = client.post(f"/api/projects/{proj.id}/meetings/rescan").json()
    assert "meetings" in rescan and "next" in rescan


def test_suggested_switch_uses_links_not_keywords(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    mgr.set_active_project(mgr.create_project(name="General Active", description="").id)
    other = mgr.create_project(name="BayaniPay", description="Client work")
    events = mock_calendar()
    mgr.sync_meeting_links(events, replace(load_settings(), api_key=None))
    suggestion = mgr.suggested_switch(events)
    assert suggestion is not None
    assert suggestion["project_id"] == other.id


def test_confidence_fn_mock_llm_path(tmp_path: Path):
    mgr = ProjectManager(tmp_path)
    proj = mgr.create_project(name="BayaniPay", description="Payments")
    event = CalendarEvent(
        id="evt_x",
        title="BayaniPay check-in",
        start="2026-12-01T10:00:00Z",
        end="2026-12-01T10:30:00Z",
        attendees=[],
    )

    def confidence_fn(ev, project, _titles):
        return 0.9 if "bayani" in ev.title.lower() and project.id == proj.id else 0.1

    settings = replace(load_settings(), api_key="test-key")
    mgr.sync_meeting_links([event], settings, confidence_fn=confidence_fn)
    linked = mgr.meetings_view(proj.id)["meetings"]
    assert linked and linked[0]["event_id"] == "evt_x"
