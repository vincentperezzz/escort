"""Choosing the watched display: listing, selection, fallback, live switch, chat commands.

Fakes only: monitors come from a fake mss list, frames from a fake grabber.
"""

import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from escort import live
from escort.capture import monitors
from escort.context import prep_chat as prep_chat_mod
from escort.context.settings_intent import PENDING_MONITOR, describe_settings, handle_settings_message
from escort.desktop import live_capture
from escort.desktop.api import create_desktop_app
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.settings import load_settings
from escort.store import MeetingStore

# Display 2 sits left of the primary, as mss reports it on Windows.
PRIMARY = {"left": 0, "top": 0, "width": 2560, "height": 1440}
LEFT = {"left": -1920, "top": 200, "width": 1920, "height": 1080}
TWO = [{"left": -1920, "top": 0, "width": 4480, "height": 1440}, PRIMARY, LEFT]
ONE = [dict(PRIMARY), dict(PRIMARY)]


class Displays:
    """A mutable fake for mss.monitors so a test can unplug a display."""

    def __init__(self, raw) -> None:
        self.raw = [dict(item) for item in raw]

    def __call__(self):
        return [dict(item) for item in self.raw]


def _runtime(tmp_path: Path, raw=TWO) -> DesktopRuntime:
    displays = Displays(raw)
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json", monitor_lister=displays)
    runtime.displays = displays  # type: ignore[attr-defined]
    return runtime


def _chat(runtime: DesktopRuntime, message: str) -> dict:
    return runtime.context_chat(message)


# --- listing and resolving ---------------------------------------------------------------


def test_describe_lists_physical_displays_with_primary_and_labels():
    listed = monitors.describe(TWO)
    assert [item["index"] for item in listed] == [1, 2]
    assert listed[0]["primary"] is True and listed[1]["primary"] is False
    assert listed[0]["label"] == "Display 1 · 2560×1440"
    assert listed[1]["label"] == "Display 2 · 1920×1080"
    assert listed[1]["left"] == -1920 and listed[1]["top"] == 200

    single = monitors.describe(ONE)
    assert len(single) == 1 and single[0]["primary"] and single[0]["name"] == "Display 1"
    assert monitors.list_monitors(lambda: (_ for _ in ()).throw(RuntimeError("no mss"))) == []


def test_primary_is_found_even_when_it_is_not_first():
    raw = [TWO[0], LEFT, PRIMARY]
    listed = monitors.describe(raw)
    assert [item["primary"] for item in listed] == [False, True]
    assert monitors.resolve("primary", listed)["index"] == 2


def test_resolve_choices_and_fallback_notice():
    listed = monitors.describe(TWO)
    assert monitors.resolve("primary", listed) == {"selected": "primary", "index": 1, "label": "Display 1", "notice": None}
    assert monitors.resolve(2, listed)["label"] == "Display 2"
    assert monitors.resolve("all", listed)["index"] == 0 and monitors.resolve("all", listed)["label"] == "All displays"
    gone = monitors.resolve(3, listed)
    assert gone["index"] == 1 and gone["selected"] == "primary"
    assert gone["notice"] == "Display 3 isn't connected any more, so Escort is watching Display 1 (primary)."


@pytest.mark.parametrize("value,expected", [("primary", "primary"), ("all", "all"), (2, 2), ("2", 2), ("main", "primary")])
def test_normalize_choice(value, expected):
    assert monitors.normalize_choice(value) == expected


@pytest.mark.parametrize("value", [0, -1, "nope", True, 2.5, None.__class__])
def test_normalize_choice_rejects_junk(value):
    with pytest.raises(ValueError):
        monitors.normalize_choice(value)


# --- runtime + API ------------------------------------------------------------------------


def test_default_is_primary_and_selection_persists(tmp_path):
    runtime = _runtime(tmp_path)
    status = runtime.monitors_status()
    assert status["selected"] == "primary" and status["effective"]["label"] == "Display 1"
    assert [item["label"] for item in status["monitors"]] == ["Display 1 · 2560×1440", "Display 2 · 1920×1080"]

    runtime.set_watch_monitor(2)
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["watch_monitor"] == 2
    again = _runtime(tmp_path)
    assert again.monitors_status()["selected"] == 2
    public = again.public_status()
    assert public["monitors"]["effective"]["label"] == "Display 2"
    assert public["prefs"]["watch_monitor"] == 2
    assert public["screen"]["monitor"] == "Display 2"


def test_saved_display_that_went_away_falls_back_to_primary_with_notice(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.set_watch_monitor(2)
    runtime.displays.raw = [dict(PRIMARY), dict(PRIMARY)]  # unplugged
    status = runtime.monitors_status(fresh=True)
    assert status["selected"] == 2, "the saved choice is kept for when it comes back"
    assert status["effective"]["index"] == 1 and status["effective"]["label"] == "Display 1"
    assert "Display 2 isn't connected" in status["notice"]
    assert runtime._watch_target()["index"] == 1


def test_monitors_api_get_post_and_validation(tmp_path):
    runtime = _runtime(tmp_path)
    client = TestClient(create_desktop_app(runtime))
    got = client.get("/api/monitors").json()
    assert got["selected"] == "primary" and len(got["monitors"]) == 2 and got["effective"]["index"] == 1

    posted = client.post("/api/monitors", json={"selected": 2}).json()
    assert posted["selected"] == 2 and posted["effective"]["label"] == "Display 2"
    assert client.post("/api/monitors", json={"selected": "all"}).json()["effective"]["label"] == "All displays"

    bad = client.post("/api/monitors", json={"selected": 5})
    assert bad.status_code == 422 and "no Display 5" in bad.json()["error"]
    assert client.post("/api/monitors", json={"selected": "sideways"}).status_code == 422
    assert client.post("/api/monitors", json={}).status_code == 422
    assert client.get("/api/monitors").json()["selected"] == "all", "rejected posts change nothing"


def test_capture_gets_a_live_monitor_source(tmp_path, no_real_capture_or_keys):
    runtime = _runtime(tmp_path)
    runtime.set_listening(True)
    source = no_real_capture_or_keys[0].kwargs["monitor_source"]
    assert source() == {"selected": "primary", "index": 1, "label": "Display 1", "notice": None}
    runtime.set_watch_monitor(2)
    assert source()["index"] == 2, "the loop re-reads the choice; no restart needed"
    runtime.set_listening(False)


def test_overlay_on_watched_display_is_reported(tmp_path):
    runtime = _runtime(tmp_path)

    class Overlay:
        supported = True
        rect = (100, 50, 540, 610)  # on the primary

        def set_visible(self, visible):
            pass

        def set_hide_from_share(self, hide):
            pass

        def window_rect(self):
            return self.rect

    overlay = Overlay()
    runtime.attach_overlay(overlay)
    assert runtime.monitors_status()["overlay_on_watched"] is None, "hidden overlay: unknown"
    runtime.set_overlay(visible=True)
    assert runtime.monitors_status()["overlay_on_watched"] is True
    runtime.set_watch_monitor(2)
    assert runtime.monitors_status()["overlay_on_watched"] is False
    runtime.set_watch_monitor("all")
    assert runtime.monitors_status()["overlay_on_watched"] is True


# --- the live screen loop switches displays -----------------------------------------------


class SwitchingGrabber:
    """Returns the same picture whatever display it is on; records set_monitor calls."""

    def __init__(self, frames: int, stop: threading.Event) -> None:
        self.left = frames
        self.stop = stop
        self.monitors: list[int] = []
        self.grabbed_on: list[int] = []

    def set_monitor(self, index):
        self.monitors.append(index)

    def grab(self):
        self.grabbed_on.append(self.monitors[-1] if self.monitors else -1)
        self.left -= 1
        if self.left <= 0:
            self.stop.set()
        return Image.new("RGB", (320, 200), "black")

    def close(self):
        pass


def test_screen_loop_switches_display_live_and_resets_the_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SCREEN_INTERVAL_S", 0)
    store = MeetingStore(tmp_path / "media")
    pipeline = live_capture.build_live_pipeline(store, load_settings(), "")
    stop = threading.Event()
    grabber = SwitchingGrabber(6, stop)
    stats = live.LiveStats()
    ticks = {"n": 0}

    def source():
        ticks["n"] += 1
        return {"index": 1, "label": "Display 1"} if ticks["n"] <= 3 else {"index": 2, "label": "Display 2"}

    live._screen_loop(pipeline, stop, stats, lambda: grabber, monitor_source=source)

    assert grabber.monitors == [1, 2], "set once at start, once on the switch, never per tick"
    assert grabber.grabbed_on == [1, 1, 1, 2, 2, 2]
    screen = stats.screen()
    assert screen["frames_seen"] == 6
    # Identical pictures: kept on the first grab, and again right after the switch (baseline reset).
    assert screen["changed_frames"] == 2
    assert screen["monitor"] == "Display 2"


def test_screen_loop_survives_a_broken_monitor_source(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SCREEN_INTERVAL_S", 0)
    store = MeetingStore(tmp_path / "media")
    pipeline = live_capture.build_live_pipeline(store, load_settings(), "")
    stop = threading.Event()
    grabber = SwitchingGrabber(2, stop)

    def broken():
        raise RuntimeError("prefs locked")

    live._screen_loop(pipeline, stop, live.LiveStats(), lambda: grabber, monitor_source=broken)
    assert grabber.grabbed_on == [-1, -1]


# --- chat intents -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("watch my second monitor", 2),
        ("monitor 2", 2),
        ("Can you watch display 2 please?", 2),
        ("bantayan mo yung pangalawang monitor", 2),
        ("use the main screen", "primary"),
        ("gamitin mo yung main screen", "primary"),
        ("watch all screens", "all"),
        ("bantayan mo lahat ng screens", "all"),
        ("switch to the other screen", 2),
    ],
)
def test_monitor_phrases_change_the_watched_display(tmp_path, phrase, expected):
    runtime = _runtime(tmp_path)
    if expected == "primary":
        runtime.set_watch_monitor(2)
    result = handle_settings_message(phrase, runtime)
    assert result is not None, phrase
    assert runtime.monitors_status()["selected"] == expected
    assert result.changed and result.changed[0]["setting"] == "watch_monitor"
    assert result.reply.startswith("Done, I'm watching")


def test_monitor_change_reply_names_the_display(tmp_path):
    runtime = _runtime(tmp_path)
    result = handle_settings_message("watch my second monitor", runtime)
    assert result.reply == "Done, I'm watching Display 2 now."
    assert result.changed == [{"setting": "watch_monitor", "value": 2, "label": "Watching Display 2"}]
    again = handle_settings_message("monitor 2", runtime)
    assert again.changed == [] and "already watching Display 2" in again.reply


def test_what_monitors_do_i_have(tmp_path):
    runtime = _runtime(tmp_path)
    result = handle_settings_message("what monitors do I have?", runtime)
    assert result.changed == []
    assert "Display 1 · 2560×1440 (primary)" in result.reply and "Display 2 · 1920×1080" in result.reply
    assert "watching Display 1" in result.reply
    single = handle_settings_message("ilang monitor meron ako?", _runtime(tmp_path / "one", raw=ONE))
    assert single.reply == "You have one display, Display 1 · 2560×1440, and I'm watching it."


def test_unspecified_switch_asks_and_short_answers_resolve_it(tmp_path):
    runtime = _runtime(tmp_path)
    question = handle_settings_message("can you change which monitor you watch?", runtime)
    assert question.changed == [] and question.pending == {"kind": PENDING_MONITOR}
    assert question.reply.startswith("Which screen should I watch?")
    assert "1 · Display 1 (primary)" in question.reply and "2 · Display 2" in question.reply
    assert runtime.monitors_status()["selected"] == "primary"

    for answer, expected in [("2", 2), ("the second one", 2), ("main", "primary"), ("yung pangalawa", 2), ("both", "all")]:
        runtime.set_watch_monitor("primary" if expected != "primary" else 2)
        result = handle_settings_message(answer, runtime, question.pending)
        assert result is not None and runtime.monitors_status()["selected"] == expected, answer
        assert result.changed

    assert handle_settings_message("2", runtime) is None, "a bare number with nothing pending is not a command"
    cancel = handle_settings_message("never mind", runtime, question.pending)
    assert cancel.reply.startswith("OK, I'll keep watching") and cancel.changed == []


def test_single_monitor_never_asks(tmp_path):
    runtime = _runtime(tmp_path, raw=ONE)
    result = handle_settings_message("switch monitors", runtime)
    assert result.pending is None and "one display" in result.reply
    missing = handle_settings_message("watch monitor 2", runtime)
    assert missing.changed == [] and "no Display 2" in missing.reply
    assert runtime.monitors_status()["selected"] == "primary"


def test_out_of_range_monitor_asks_again(tmp_path):
    runtime = _runtime(tmp_path)
    result = handle_settings_message("watch monitor 3", runtime)
    assert result.changed == [] and result.pending and "no Display 3" in result.reply


@pytest.mark.parametrize(
    "phrase,start,expected",
    [
        ("make the overlay more transparent", 0.92, 0.77),
        ("mas transparent yung overlay", 0.92, 0.77),
        ("make the overlay less transparent", 0.6, 0.75),
        ("60% opacity", 0.92, 0.6),
        ("set overlay opacity to 45", 0.92, 0.45),
        ("make the overlay 70% transparent", 0.92, 0.3),
        ("overlay opacity 10%", 0.92, 0.3),
    ],
)
def test_opacity_phrases(tmp_path, phrase, start, expected):
    runtime = _runtime(tmp_path)
    runtime.set_overlay(opacity=start)
    result = handle_settings_message(phrase, runtime)
    assert result is not None, phrase
    assert runtime.overlay_opacity == pytest.approx(expected)
    assert result.changed == [{"setting": "overlay_opacity", "value": runtime.overlay_opacity, "label": f"Overlay {round(expected * 100)}% opacity"}]
    assert "opacity now" in result.reply


@pytest.mark.parametrize(
    "phrase,start,expected",
    [
        ("hide the overlay from screen share", False, True),
        ("itago mo yung overlay sa screen share", False, True),
        ("show it in the share", True, False),
        ("stop hiding the overlay from screen sharing", True, False),
    ],
)
def test_share_phrases(tmp_path, phrase, start, expected):
    runtime = _runtime(tmp_path)
    runtime.set_overlay(hide_from_share=start)
    result = handle_settings_message(phrase, runtime)
    assert result is not None, phrase
    assert runtime.hide_from_share is expected
    assert result.changed[0]["setting"] == "hide_from_share" and result.changed[0]["value"] is expected


@pytest.mark.parametrize(
    "phrase",
    [
        "What should I watch for?",
        "What should I say if they share their screen?",
        "How can I be more transparent about the fees?",
        "Should I hide my notes when screen sharing?",
        "Ano ang dapat kong bantayan sa BayaniPay call?",
        "What's the main risk on this call?",
        "Draft my opening line",
    ],
)
def test_ordinary_questions_change_nothing(tmp_path, phrase):
    runtime = _runtime(tmp_path)
    before = (runtime.monitors_status()["selected"], runtime.overlay_opacity, runtime.hide_from_share)
    assert handle_settings_message(phrase, runtime) is None
    assert (runtime.monitors_status()["selected"], runtime.overlay_opacity, runtime.hide_from_share) == before


# --- chat endpoint ------------------------------------------------------------------------


def test_chat_endpoint_returns_changed_and_keeps_the_pending_question(tmp_path):
    runtime = _runtime(tmp_path)
    client = TestClient(create_desktop_app(runtime))

    asked = client.post("/api/context/chat", json={"message": "I want to switch monitors"}).json()
    assert asked["changed"] == [] and "Which screen should I watch?" in asked["reply"]

    done = client.post("/api/context/chat", json={"message": "the second one"}).json()
    assert done["reply"] == "Done, I'm watching Display 2 now."
    assert done["changed"] == [{"setting": "watch_monitor", "value": 2, "label": "Watching Display 2"}]
    assert done["context"]["chat"][-1]["changed"] == done["changed"]
    assert client.get("/api/status").json()["screen"]["monitor"] == "Display 2"

    # Pending was consumed: a later bare number goes to Muse (no key here), nothing changes.
    muse = client.post("/api/context/chat", json={"message": "1"}).json()
    assert muse["changed"] == [] and "MODEL_API_KEY" in muse["reply"]
    assert runtime.monitors_status()["selected"] == 2

    opacity = client.post("/api/context/chat", json={"message": "overlay 60% opacity"}).json()
    assert opacity["changed"][0]["setting"] == "overlay_opacity" and runtime.overlay_opacity == 0.6


def test_muse_fallback_prompt_lists_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Watch the fees."}}]})

    runtime = _runtime(tmp_path)
    note = describe_settings(runtime)
    reply = prep_chat_mod.prep_chat("What should I watch for?", "", [], http=httpx.Client(transport=httpx.MockTransport(handler)), settings_note=note)
    assert reply == "Watch the fees."
    system = seen[0]["messages"][0]["content"]
    assert "which screen Escort watches: now Display 1" in system
    assert "overlay opacity, now 92%" in system and "hiding the overlay from screen share, now on" in system
