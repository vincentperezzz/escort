import json
from datetime import datetime, timedelta, timezone

import httpx
from PIL import Image, ImageDraw

from escort.context.sources.google_source import event_meeting_url
from escort.desktop.runtime import demo_runtime
from escort.models import CardRecord, NudgeRecord
from escort.proactive import NO_MEETING_AFTER_S, ProactiveEngine, build_proactive_request
from escort.settings import load_settings
from escort.store import MeetingStore, new_id, utc_now

NOW = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)
SALES = {"id": "p_sales", "name": "Sales CRM", "description": "bnext-sales-crm: leads, deals, RBAC"}
ESCORT = {"id": "p_escort", "name": "Escort", "description": "meeting copilot, Google APIs"}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _engine(store, *, event=None, window=None, api_key=None, client=None, gate=None, others=(ESCORT,)):
    clock = Clock()
    settings = load_settings()
    if api_key:
        from dataclasses import replace

        settings = replace(settings, api_key=api_key, base_url="https://muse.test/v1")
    pushed: list[NudgeRecord] = []
    engine = ProactiveEngine(
        store,
        settings,
        lambda: {"project": SALES, "other_projects": list(others), "event": event, "notes": "RBAC: admin, sales rep"},
        gate_fn=(lambda: gate) if gate else None,
        foreground_fn=lambda: window,
        on_nudge=pushed.append,
        client=client,
        clock=clock,
        now_fn=lambda: NOW,
    )
    return engine, clock, pushed


def _say(store, speaker, text):
    store.add_utterance(speaker=speaker, text=text, start_ms=0, end_ms=0, finished_at=0.0)


def test_no_meeting_offers_the_join_link_after_a_minute_of_silence(tmp_path):
    store = MeetingStore(tmp_path)
    event = {"title": "Sales CRM sync", "start": (NOW - timedelta(minutes=3)).isoformat(), "end": (NOW + timedelta(minutes=27)).isoformat(), "meeting_url": "https://meet.google.com/abc-defg-hij"}
    engine, clock, pushed = _engine(store, event=event)
    engine.tick()
    assert not store.nudges
    clock.t += NO_MEETING_AFTER_S + 1
    engine.tick()
    engine.tick()
    assert len(store.nudges) == 1
    nudge = store.nudges[0]
    assert nudge.kind == "NO_MEETING" and nudge.priority == "urgent"
    assert {"id": "open_link", "label": "Join meeting", "value": "https://meet.google.com/abc-defg-hij"} in nudge.actions
    assert pushed == [nudge]


def test_no_meeting_without_link_suggests_checking_team_messages(tmp_path):
    store = MeetingStore(tmp_path)
    engine, clock, _ = _engine(store, event={"title": "Sales CRM sync", "start": NOW.isoformat(), "end": (NOW + timedelta(hours=1)).isoformat(), "meeting_url": ""})
    clock.t += NO_MEETING_AFTER_S + 1
    engine.tick()
    assert "team messages" in store.nudges[0].body
    assert not any(a["id"] == "open_link" for a in store.nudges[0].actions)


def test_no_meeting_stays_quiet_once_the_other_side_speaks(tmp_path):
    store = MeetingStore(tmp_path)
    engine, clock, _ = _engine(store)
    _say(store, "Them", "Hi, can you hear me?")
    clock.t += NO_MEETING_AFTER_S * 3
    engine.tick()
    assert not store.nudges


def _commitment(store, what, when):
    store.add_card(
        CardRecord(
            id=new_id("card"), type="COMMITMENT", title="Promise", body=what, prd_section=None, who="Jessa", what=what,
            utterance_id=None, trigger_text=what, created_at=utc_now(), latency_ms=0, crop_path=None, crop_url=None,
            source="rules", deictic=None, segmenter=None, when=when,
        )
    )


def test_rules_note_a_dated_promise_and_ask_to_pin_down_a_vague_one(tmp_path):
    store = MeetingStore(tmp_path)
    engine, clock, _ = _engine(store)
    _say(store, "Them", "hello")
    _commitment(store, "share the RBAC role matrix", "Friday")
    engine.tick()
    clock.t += 60
    _commitment(store, "look into the admin roles soon", None)
    engine.tick()
    kinds = [n.kind for n in store.nudges]
    assert kinds == ["NOTED", "CLARIFY"]
    assert store.nudges[0].priority == "low"
    assert "who owns this and by when" in store.nudges[1].body


def test_window_title_naming_another_project_raises_wrong_project(tmp_path):
    store = MeetingStore(tmp_path)
    engine, _, pushed = _engine(store, window={"title": "proactive.py - escort - Cursor", "process": "Cursor.exe"})
    engine.tick()
    engine.tick()
    assert [n.kind for n in store.nudges] == ["WRONG_PROJECT"]
    assert store.nudges[0].actions[0] == {"id": "switch_project", "label": "Switch to Escort", "value": "p_escort"}
    assert pushed[0].priority == "urgent"


def test_window_title_with_the_active_project_is_fine(tmp_path):
    store = MeetingStore(tmp_path)
    engine, _, _ = _engine(store, window={"title": "bnext-sales-crm - Sales CRM - Cursor", "process": "Cursor.exe"})
    engine.tick()
    assert not store.nudges


class Gate:
    scene_cut = True


def _frame(store, color="white", chart=False):
    image = Image.new("RGB", (640, 360), color)
    if chart:
        draw = ImageDraw.Draw(image)
        for i, h in enumerate((80, 140, 200, 120)):
            draw.rectangle((60 + i * 120, 320 - h, 140 + i * 120, 320), fill="navy")
    path = store.save_png(image, "frame")
    return store.add_frame(path=path, phash="0" * 16)


def _muse(replies):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        body = replies[min(len(calls) - 1, len(replies) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


UNRELATED = {
    "screen": {"subject": "escort repo in Cursor, Google API client code", "project_relevance": "unrelated", "matching_project_id": None, "confidence": 0.8},
    "visual": {"keep": False, "kind": "other", "caption": ""},
    "nudges": [
        {
            "kind": "WRONG_PROJECT", "priority": "urgent", "title": "This looks like another repo", "body": "You're showing escort, not bnext-sales-crm.",
            "actions": [{"id": "switch_project", "label": "Switch", "value": "p_made_up"}, {"id": "dismiss", "label": "It's related", "value": None}],
            "reason": "repo name", "confidence": 0.8, "key": "wrong-repo",
        }
    ],
}


def test_model_needs_two_confident_looks_before_calling_the_wrong_project(tmp_path):
    store = MeetingStore(tmp_path)
    client, calls = _muse([UNRELATED])
    engine, clock, _ = _engine(store, api_key="k", client=client, gate=Gate())
    _frame(store)
    engine.tick()
    assert len(calls) == 1 and not store.nudges
    request = calls[0]["messages"][1]["content"]
    assert request[1]["type"] == "image_url"
    assert "Sales CRM" in request[0]["text"] and "Escort" in request[0]["text"]
    clock.t += 30
    _frame(store, "#eeeeee")
    engine.tick()
    assert len(calls) == 2
    assert [n.kind for n in store.nudges] == ["WRONG_PROJECT"]
    # A project id the model made up never becomes a button.
    assert [a["id"] for a in store.nudges[0].actions] == ["dismiss"]


def test_model_keeps_a_chart_once_and_drops_low_confidence_nudges(tmp_path):
    store = MeetingStore(tmp_path)
    reply = {
        "screen": {"subject": "Q3 pipeline bar chart", "project_relevance": "matches", "matching_project_id": None, "confidence": 0.9},
        "visual": {"keep": True, "kind": "chart", "caption": "Q3 pipeline by stage; proposals stage is the bottleneck."},
        "nudges": [
            {"kind": "NOTED", "priority": "low", "title": "Noted: Jessa on RBAC scope", "body": "Admins only can export leads.", "actions": [], "reason": "", "confidence": 0.9, "key": "rbac-export"},
            {"kind": "HEADS_UP", "priority": "normal", "title": "Maybe something", "body": "Not sure.", "actions": [], "reason": "", "confidence": 0.3, "key": "meh"},
        ],
    }
    client, calls = _muse([reply])
    engine, clock, _ = _engine(store, api_key="k", client=client, gate=Gate())
    _frame(store, chart=True)
    engine.tick()
    clock.t += 30
    _frame(store, chart=True)  # same chart again, new frame id
    engine.tick()
    assert len(calls) == 2
    assert len(store.snapshots) == 1 and store.snapshots[0].kind == "chart"
    assert [n.title for n in store.nudges] == ["Noted: Jessa on RBAC scope"]
    assert store.snapshot()["snapshots"][0]["url"].startswith("/panel/media/visual_")


def test_model_is_not_called_when_nothing_changed(tmp_path):
    store = MeetingStore(tmp_path)
    client, calls = _muse([{"screen": {}, "visual": {"keep": False, "kind": "other", "caption": ""}, "nudges": []}])
    engine, clock, _ = _engine(store, api_key="k", client=client)
    _say(store, "Them", "Let's review the RBAC scope.")
    engine.tick()
    for _ in range(5):
        clock.t += 15
        engine.tick()
    assert len(calls) == 1


def test_request_shape():
    request = build_proactive_request("muse-spark-1.3", {"a": 1}, None)
    assert request["response_format"]["json_schema"]["name"] == "ProactiveAssessment"
    assert "Silence is the default" in request["messages"][0]["content"]


def test_nudge_actions_use_stored_values(tmp_path, monkeypatch):
    runtime = demo_runtime(tmp_path)
    other = runtime.projects.create_project("Sales CRM")
    runtime.set_listening(True)
    try:
        opened = []
        monkeypatch.setattr("webbrowser.open", opened.append)
        nudge = runtime.store.add_nudge(
            NudgeRecord(
                id="ndg_1", kind="NO_MEETING", priority="urgent", title="t", body="b", created_at=utc_now(), source="rules", key="k",
                actions=[{"id": "open_link", "label": "Join", "value": "https://meet.google.com/abc"}, {"id": "switch_project", "label": "Switch", "value": other.id}],
            )
        )
        assert runtime.nudge_action(nudge.id, "open_link")["ok"]
        assert opened == ["https://meet.google.com/abc"]
        assert runtime.nudge_action(nudge.id, "ask")["ok"] is False
        assert runtime.nudge_action(nudge.id, "switch_project")["ok"]
        assert runtime.projects.get_active_project().id == other.id
        assert runtime._listen_project_id == other.id
        runtime.nudge_action(nudge.id, "dismiss")
        assert runtime.store.nudge(nudge.id).status == "dismissed"
    finally:
        runtime.set_listening(False)


def test_loud_nudge_opens_a_collapsed_overlay(tmp_path):
    runtime = demo_runtime(tmp_path)
    runtime.set_listening(True)
    try:
        runtime.set_overlay(collapsed=True)
        quiet = NudgeRecord(id="n1", kind="NOTED", priority="low", title="t", body="b", actions=[], created_at=utc_now(), source="model", key="a")
        runtime._on_nudge(quiet)
        assert runtime.overlay_collapsed is True
        runtime._on_nudge(NudgeRecord(id="n2", kind="CLARIFY", priority="normal", title="t", body="b", actions=[], created_at=utc_now(), source="model", key="b"))
        assert runtime.overlay_collapsed is False and runtime.overlay_visible is True
    finally:
        runtime.set_listening(False)


def test_session_keeps_visuals_after_the_live_media_is_wiped(tmp_path):
    runtime = demo_runtime(tmp_path)
    project = runtime.projects.get_active_project() or runtime.projects.create_project("Sales CRM")
    sessions = runtime.projects.session_store(project.id)
    visual = runtime.store.add_snapshot(Image.new("RGB", (40, 30), "navy"), caption="Funnel chart", kind="chart")
    kept = sessions.keep_visuals("sess_abc", [visual.to_dict()], runtime.store.media_dir)
    sessions.save(session_id="sess_abc", project_id=project.id, title="t", started_at=utc_now(), ended_at=utc_now(), summary={"bullets": []}, snapshot={"snapshots": kept})
    runtime.store.delete_meeting()
    payload = sessions.recap_payload("sess_abc")
    assert payload["snapshots"][0]["url"] == f"/api/sessions/sess_abc/media/{visual.url.rsplit('/', 1)[1]}"
    assert runtime.session_media("sess_abc", visual.url.rsplit("/", 1)[1]).is_file()
    assert runtime.session_media("sess_abc", "../config.json") is None
    sessions.delete("sess_abc")
    assert not sessions.media_dir("sess_abc").exists()


def test_event_meeting_url_sources():
    assert event_meeting_url({"hangoutLink": "https://meet.google.com/abc-defg-hij"}) == "https://meet.google.com/abc-defg-hij"
    assert event_meeting_url({"conferenceData": {"entryPoints": [{"entryPointType": "phone", "uri": "tel:1"}, {"entryPointType": "video", "uri": "https://x.zoom.us/j/1"}]}}) == "https://x.zoom.us/j/1"
    assert event_meeting_url({"description": '<a href="https://teams.microsoft.com/l/meetup-join/19%3a/0?c=1">Join</a>'}) == "https://teams.microsoft.com/l/meetup-join/19%3a/0?c=1"
    assert event_meeting_url({"location": "Room 4"}) == ""
