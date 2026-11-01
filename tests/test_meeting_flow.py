"""Desktop meeting flow: live capture wiring, overlay sizing/focus/opacity, main window, summary.

Everything runs on fakes: no audio device, screen grab, network or real Win32 call.
"""

import json
import threading
import time
import types
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from escort import live
from escort.capture.windows_audio import CaptureUnavailable, choose_loopback_device, choose_mic_device, parse_device_id
from escort.desktop import live_capture, win_overlay
from escort.desktop import overlay as overlay_mod
from escort.desktop.api import create_desktop_app
from escort.desktop.overlay import COLLAPSED_HEIGHT, HEIGHT, WIDTH, OverlayWindow, clamp_opacity
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.models import CardRecord
from escort.settings import load_settings
from escort.store import MeetingStore, new_id, utc_now


class FakeController:
    def __init__(self) -> None:
        self.supported = True
        self.calls: list[tuple[str, object]] = []

    def set_visible(self, visible):
        self.calls.append(("visible", visible))

    def set_hide_from_share(self, hide):
        self.calls.append(("hide", hide))

    def set_collapsed(self, collapsed):
        self.calls.append(("collapsed", collapsed))

    def set_opacity(self, opacity):
        self.calls.append(("opacity", opacity))

    def set_input_focus(self, focus):
        self.calls.append(("focus", focus))


class FakeMain:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")


def _runtime(tmp_path: Path, **kwargs) -> DesktopRuntime:
    return DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json", **kwargs)


def _wait_summary(runtime: DesktopRuntime) -> dict:
    if runtime._summary_thread is not None:
        runtime._summary_thread.join(timeout=10)
    return runtime.summary_status()


def _add_turn(store: MeetingStore, speaker: str, text: str) -> None:
    store.add_utterance(speaker=speaker, text=text, start_ms=0, end_ms=1000, finished_at=time.perf_counter())


def _add_card(store: MeetingStore, card_type: str, **fields) -> None:
    base = dict(
        id=new_id("card"), type=card_type, title="t", body="b", prd_section=None, who=None, what=None,
        utterance_id=None, trigger_text=None, created_at=utc_now(), latency_ms=0, crop_path=None,
        crop_url=None, source="test", deictic=None, segmenter=None,
    )
    base.update(fields)
    store.add_card(CardRecord(**base))


# --- A: live capture is wired into set_listening -----------------------------------------


def test_start_listening_starts_capture_with_chosen_devices_and_notes(tmp_path, no_real_capture_or_keys):
    runtime = _runtime(tmp_path)
    runtime.save_setup({"mic_id": "3", "loopback_id": "7"})
    runtime.set_prd("## Fees\nNo new fees.\n", "scope.md")
    client = TestClient(create_desktop_app(runtime))

    on = client.post("/api/listen", json={"on": True}).json()
    assert len(no_real_capture_or_keys) == 1
    session = no_real_capture_or_keys[0]
    assert session.kwargs["mic_id"] == "3" and session.kwargs["loopback_id"] == "7"
    assert "No new fees" in session.prd_text
    assert session.store is runtime.store
    assert on["listening_started_at"] and datetime.fromisoformat(on["listening_started_at"])
    assert on["screen"] == {
        "monitoring": True, "last_frame_at": None, "frames_seen": 0, "changed_frames": 0, "monitor": "Display 1",
    }
    assert callable(session.kwargs["monitor_source"])
    assert on["capture_error"] is None

    client.post("/api/listen", json={"on": True})
    assert len(no_real_capture_or_keys) == 1, "a second start must not spawn another capture"

    off = client.post("/api/listen", json={"on": False}).json()
    assert off["listening"] is False and off["listening_started_at"] is None
    _wait_summary(runtime)
    assert session.stopped.is_set()
    assert runtime.public_status()["screen"]["monitoring"] is False


def test_delete_and_shutdown_stop_capture(tmp_path, no_real_capture_or_keys):
    runtime = _runtime(tmp_path)
    runtime.set_listening(True)
    runtime.delete_meeting()
    assert no_real_capture_or_keys[0].stopped.is_set()
    assert runtime.public_status()["screen"]["frames_seen"] == 0
    runtime.set_listening(True)
    runtime.shutdown()
    assert no_real_capture_or_keys[1].stopped.is_set()


def test_capture_start_failure_is_soft(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("no devices at all")

    monkeypatch.setattr(live_capture, "start_session", boom)
    runtime = _runtime(tmp_path)
    status = TestClient(create_desktop_app(runtime)).post("/api/listen", json={"on": True})
    assert status.status_code == 200 and status.json()["listening"] is True
    assert any("no devices at all" in w for w in runtime.store.warnings)


class SequenceGrabber:
    """Returns the given images in order, then sets stop."""

    def __init__(self, images, stop: threading.Event) -> None:
        self.images = list(images)
        self.stop = stop
        self.closed = False

    def grab(self):
        image = self.images.pop(0)
        if not self.images:
            self.stop.set()
        return image

    def close(self):
        self.closed = True


def test_screen_loop_samples_and_counts_only_changed_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SCREEN_INTERVAL_S", 0)
    store = MeetingStore(tmp_path / "media")
    pipeline = live_capture.build_live_pipeline(store, load_settings(), "")
    black = Image.new("RGB", (320, 200), "black")
    split = Image.new("RGB", (320, 200), "black")
    split.paste(Image.new("RGB", (160, 200), "white"), (0, 0))
    stop = threading.Event()
    grabber = SequenceGrabber([black, black.copy(), split, split.copy()], stop)
    stats = live.LiveStats()

    live._screen_loop(pipeline, stop, stats, lambda: grabber)

    screen = stats.screen()
    assert screen["frames_seen"] == 4
    assert screen["changed_frames"] == 2
    assert screen["monitoring"] is False and screen["last_frame_at"]
    assert store.skipped_frames == 2 and len(store.frames) == 1
    assert grabber.closed


def test_live_session_reports_missing_mss_and_missing_key(tmp_path, monkeypatch):
    monkeypatch.setattr(live_capture, "resolve_transcriber_mode", lambda mode, settings: "scripted")

    def no_mss():
        raise CaptureUnavailable("mss is required for screen frames")

    store = MeetingStore(tmp_path / "media")
    session = live_capture.LiveSession(store, load_settings(), "", grabber_factory=no_mss).start()
    for thread in session.threads:
        thread.join(timeout=5)
    error = session.error()
    assert "Screen capture unavailable" in error and "mss" in error
    assert "Meta API key" in error
    assert session.screen()["monitoring"] is False
    session.stop()


class FakeAudio:
    def __init__(self) -> None:
        self.opened_with = None
        self.closed = False
        self.chunks = {"Me": [b"\x01\x00" * 480], "Them": [b"\x02\x00" * 480]}

    def open(self, mic_index=None, loopback_index=None):
        self.opened_with = (mic_index, loopback_index)

    def speakers(self):
        return ["Me", "Them"]

    def read(self, speaker, block=True):
        assert block is False, "the live loop must not block on a silent loopback stream"
        queue = self.chunks[speaker]
        return queue.pop(0) if queue else b""

    def close(self):
        self.closed = True


class FakeAsr:
    instances: list = []

    def __init__(self, settings, speaker, on_final, on_partial=None, keywords=None) -> None:
        self.speaker = speaker
        self.written: list[bytes] = []
        self.closed = False
        FakeAsr.instances.append(self)

    def start(self):
        pass

    def write(self, pcm):
        self.written.append(pcm)

    def close(self):
        self.closed = True


def test_live_session_streams_both_speakers_from_chosen_devices(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv("TAGLISH_SPARK_INTERVAL", "3600")
    FakeAsr.instances = []
    audio = FakeAudio()
    store = MeetingStore(tmp_path / "media")
    stop_grabber = threading.Event()
    session = live_capture.LiveSession(
        store,
        load_settings(),
        "## Fees\nNo fees.",
        mic_id="3",
        loopback_id="default-loop",
        grabber_factory=lambda: SequenceGrabber([Image.new("RGB", (64, 64))], stop_grabber),
        audio_factory=lambda: audio,
        session_factory=FakeAsr,
    ).start()
    deadline = time.time() + 5
    while time.time() < deadline and not all(item.written for item in FakeAsr.instances):
        time.sleep(0.01)
    session.stop(timeout=5)

    assert audio.opened_with == (3, None)
    assert {item.speaker for item in FakeAsr.instances} == {"Me", "Them"}
    assert all(item.written and item.closed for item in FakeAsr.instances)
    assert audio.closed
    assert session.error() is None
    assert store.mode == "live"


def test_device_choice_respects_settings_and_falls_back():
    devices = [
        {"index": 0, "name": "Mic A", "maxInputChannels": 1},
        {"index": 1, "name": "Mic B", "maxInputChannels": 2},
        {"index": 2, "name": "Speakers", "maxInputChannels": 0},
        {"index": 3, "name": "Speakers [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True},
        {"index": 4, "name": "Headset [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True},
    ]
    assert choose_mic_device(devices, 0, preferred=1)["index"] == 1
    assert choose_mic_device(devices, 0, preferred=2)["index"] == 0, "an output is not a mic"
    assert choose_mic_device(devices, 0, preferred=None)["index"] == 0
    assert choose_loopback_device(devices, 2, preferred=4)["index"] == 4
    assert choose_loopback_device(devices, 2, preferred=1)["index"] == 3, "a mic is not a loopback"
    assert parse_device_id("7") == 7 and parse_device_id("default-mic") is None and parse_device_id("") is None


# --- B/C/D: overlay collapse, input focus, opacity ---------------------------------------


def test_overlay_collapse_focus_and_opacity_via_api(tmp_path):
    runtime = _runtime(tmp_path)
    fake = FakeController()
    runtime.attach_overlay(fake)
    assert ("opacity", 0.92) in fake.calls and ("collapsed", False) in fake.calls
    client = TestClient(create_desktop_app(runtime))

    collapsed = client.post("/api/overlay", json={"collapsed": True}).json()
    assert collapsed["collapsed"] is True and fake.calls[-1] == ("collapsed", True)
    assert client.get("/api/overlay").json()["collapsed"] is True

    # Focus is ignored while the overlay is hidden, accepted once visible.
    assert client.post("/api/overlay", json={"input_focus": True}).json()["input_focus"] is False
    client.post("/api/overlay", json={"visible": True})
    assert client.post("/api/overlay", json={"input_focus": True}).json()["input_focus"] is True
    assert fake.calls[-1] == ("focus", True)
    assert client.post("/api/overlay", json={"input_focus": False}).json()["input_focus"] is False
    assert fake.calls[-1] == ("focus", False)
    client.post("/api/overlay", json={"input_focus": True})
    hidden = client.post("/api/overlay", json={"visible": False}).json()
    assert hidden["input_focus"] is False

    assert client.post("/api/overlay", json={"opacity": 0.75}).json()["opacity"] == 0.75
    assert fake.calls[-1] == ("opacity", 0.75)
    assert client.post("/api/overlay", json={"opacity": 0.05}).json()["opacity"] == 0.3
    assert client.post("/api/overlay", json={"opacity": 7}).json()["opacity"] == 1.0
    assert client.post("/api/overlay", json={"opacity": "nope"}).status_code == 422


def test_opacity_persists_and_defaults(tmp_path):
    runtime = _runtime(tmp_path)
    assert runtime.overlay_opacity == 0.92
    runtime.set_overlay(opacity=0.6)
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["overlay_opacity"] == 0.6
    assert _runtime(tmp_path).overlay_status()["opacity"] == 0.6
    assert clamp_opacity(float("nan")) == 0.92 and clamp_opacity(None) == 0.92


def test_old_controllers_without_new_setters_still_work(tmp_path):
    class Minimal:
        supported = False

        def set_visible(self, visible):
            pass

        def set_hide_from_share(self, hide):
            pass

    runtime = _runtime(tmp_path)
    runtime.attach_overlay(Minimal())
    status = runtime.set_overlay(collapsed=True, opacity=0.5, visible=True, input_focus=True)
    assert status["collapsed"] is True and status["opacity"] == 0.5 and status["input_focus"] is True


class SizingUser32:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def GetDpiForWindow(self, hwnd):
        return 144  # 150 %

    def SetWindowPos(self, *args):
        self.calls.append(("SetWindowPos",) + args)
        return 1

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name,) + args)
            return 1

        return call


@pytest.fixture
def fake_windows(monkeypatch):
    user32 = SizingUser32()
    monkeypatch.setattr(win_overlay, "_USER32", user32)
    monkeypatch.setattr(win_overlay, "is_windows", lambda: True)
    monkeypatch.setattr(win_overlay, "windows_build", lambda: 22631)
    monkeypatch.setattr(win_overlay, "round_corners", lambda hwnd: True)
    return user32


def test_overlay_window_resizes_to_pill_and_back_keeping_position(fake_windows):
    controller = OverlayWindow(types.SimpleNamespace(native=None))
    controller.hwnd = 1234
    controller.set_collapsed(True)
    controller.set_collapsed(False)
    sizes = [call for call in fake_windows.calls if call[0] == "SetWindowPos"]
    flags = win_overlay.SWP_NOMOVE | win_overlay.SWP_NOZORDER | win_overlay.SWP_NOACTIVATE
    assert sizes[0][1:] == (1234, None, 0, 0, int(WIDTH * 1.5), int(COLLAPSED_HEIGHT * 1.5), flags)
    assert sizes[1][1:] == (1234, None, 0, 0, int(WIDTH * 1.5), int(HEIGHT * 1.5), flags)


def test_overlay_window_without_handle_uses_pywebview_resize(monkeypatch):
    monkeypatch.setattr(win_overlay, "is_windows", lambda: False)
    sizes = []
    window = types.SimpleNamespace(native=None, resize=lambda w, h: sizes.append((w, h)))
    controller = OverlayWindow(window)
    controller.set_collapsed(True)
    assert sizes == [(WIDTH, COLLAPSED_HEIGHT)]


def test_input_focus_toggles_noactivate_and_returns_focus(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(win_overlay, "foreground_window", lambda: 999)
    monkeypatch.setattr(win_overlay, "is_window", lambda hwnd: hwnd in (999, 1234))
    monkeypatch.setattr(overlay_mod, "FOCUS_RELEASE_DELAY_S", 0.01)
    monkeypatch.setattr(win_overlay, "make_tool_window", lambda hwnd, no_activate=True: events.append(("style", hwnd, no_activate)))
    monkeypatch.setattr(win_overlay, "focus_window", lambda hwnd: events.append(("focus", hwnd)) or True)
    monkeypatch.setattr(win_overlay, "show_no_activate", lambda *a, **k: True)
    monkeypatch.setattr(win_overlay, "hide", lambda hwnd: True)

    controller = OverlayWindow(types.SimpleNamespace(native=None))
    controller.hwnd = 1234
    controller.set_input_focus(True)
    assert events == [("style", 1234, False), ("focus", 1234)]

    events.clear()
    controller.set_visible(True)  # re-showing while typing must not re-add NOACTIVATE
    assert ("style", 1234, False) in events

    events.clear()
    controller.set_input_focus(False)
    _wait_release(controller)
    assert events == [("style", 1234, True), ("focus", 999)]

    events.clear()
    controller.set_input_focus(True)
    controller.set_visible(False)  # hiding releases focus too
    assert ("style", 1234, True) in events and ("focus", 999) in events


def _wait_release(controller: OverlayWindow) -> None:
    timer = controller._release_timer
    if timer is not None:
        timer.join(2)


def _focus_recorder(monkeypatch) -> list[tuple]:
    events: list[tuple] = []
    monkeypatch.setattr(win_overlay, "foreground_window", lambda: 999)
    monkeypatch.setattr(win_overlay, "is_window", lambda hwnd: True)
    monkeypatch.setattr(win_overlay, "make_tool_window", lambda hwnd, no_activate=True: events.append(("style", hwnd, no_activate)))
    monkeypatch.setattr(win_overlay, "focus_window", lambda hwnd: events.append(("focus", hwnd)) or True)
    return events


def test_repeated_input_focus_is_a_no_op(monkeypatch):
    """Each re-run of take-focus activated the Form again, which made the page blur/refocus: the flicker loop."""
    events = _focus_recorder(monkeypatch)
    controller = OverlayWindow(types.SimpleNamespace(native=None))
    controller.hwnd = 1234
    controller.set_input_focus(True)
    events.clear()
    controller.set_input_focus(True)
    controller.set_input_focus(True)
    assert events == []


def test_blur_then_focus_churn_never_releases(monkeypatch):
    events = _focus_recorder(monkeypatch)
    monkeypatch.setattr(overlay_mod, "FOCUS_RELEASE_DELAY_S", 0.05)
    controller = OverlayWindow(types.SimpleNamespace(native=None))
    controller.hwnd = 1234
    controller.set_input_focus(True)
    events.clear()
    controller.set_input_focus(False)
    controller.set_input_focus(True)  # the page refocused the box before the release fired
    time.sleep(0.15)
    assert events == [] and controller._input_focus is True


def test_gui_calls_skip_a_destroyed_window(monkeypatch):
    monkeypatch.setattr(win_overlay, "is_windows", lambda: True)
    monkeypatch.setattr(win_overlay, "is_window", lambda hwnd: False)
    resized = []
    monkeypatch.setattr(win_overlay, "resize", lambda *a: resized.append(a) or True)
    controller = OverlayWindow(types.SimpleNamespace(native=None))
    controller.hwnd = 1234
    controller.set_collapsed(True)
    assert resized == []


def test_gui_calls_skip_a_disposed_or_unbuilt_form(monkeypatch):
    monkeypatch.setattr(win_overlay, "is_windows", lambda: True)
    monkeypatch.setattr(win_overlay, "is_window", lambda hwnd: True)
    for native in (
        types.SimpleNamespace(Opacity=1.0, InvokeRequired=False, IsDisposed=True, IsHandleCreated=True),
        types.SimpleNamespace(Opacity=1.0, InvokeRequired=False, IsDisposed=False, IsHandleCreated=False),
    ):
        controller = OverlayWindow(types.SimpleNamespace(native=native))
        controller.hwnd = 1234
        controller.set_opacity(0.5)
        assert native.Opacity == 1.0


def test_gui_calls_are_marshalled_through_invoke_off_the_ui_thread(monkeypatch):
    """Off the UI thread every WinForms/HWND call goes through Form.Invoke, never runs on the caller."""
    import sys as _sys

    monkeypatch.setattr(win_overlay, "is_window", lambda hwnd: True)
    monkeypatch.setattr(win_overlay, "make_tool_window", lambda *a, **k: True)
    monkeypatch.setattr(win_overlay, "set_capture_excluded", lambda *a: True)
    fake_system = types.ModuleType("System")

    class _Generic:
        def __getitem__(self, _type):
            return lambda fn: fn

    fake_system.Func = _Generic()
    fake_system.Type = object
    monkeypatch.setitem(_sys.modules, "System", fake_system)
    invoked = []

    class Form:
        InvokeRequired = True
        IsDisposed = False
        IsHandleCreated = True
        Opacity = 1.0

        def Invoke(self, delegate):
            invoked.append(threading.current_thread().name)
            delegate()

    native = Form()
    controller = OverlayWindow(types.SimpleNamespace(native=native))
    controller.hwnd = 1234
    controller.set_opacity(0.6)
    assert invoked and native.Opacity == 0.6


def test_opacity_sweep_skips_repeated_values(fake_windows):
    sets = []

    class Native:
        InvokeRequired = False

        @property
        def Opacity(self):
            return sets[-1] if sets else 1.0

        @Opacity.setter
        def Opacity(self, value):
            sets.append(value)

    controller = OverlayWindow(types.SimpleNamespace(native=Native()))
    controller.hwnd = 1234
    for value in (0.5, 0.5, 0.5, 0.6, 0.6, 0.5):
        controller.set_opacity(value)
    assert sets == [0.5, 0.6, 0.5]


def test_overlay_window_is_created_without_easy_drag():
    """easy_drag made a mousedown anywhere drag the window, so the opacity slider could not be dragged."""
    created = {}

    class FakeEvents:
        def __init__(self):
            self.before_show = self
            self.loaded = self

        def __iadd__(self, _handler):
            return self

    def create_window(*args, **kwargs):
        created.update(kwargs)
        return types.SimpleNamespace(events=FakeEvents(), native=None)

    overlay_mod.create_overlay(types.SimpleNamespace(create_window=create_window, screens=[]), "http://x")
    assert created["easy_drag"] is False and created["frameless"] is True


def test_make_tool_window_style_bits(fake_windows):
    styles = []
    fake_windows.GetWindowLongPtrW = lambda hwnd, index: win_overlay.WS_EX_APPWINDOW | win_overlay.WS_EX_NOACTIVATE
    fake_windows.SetWindowLongPtrW = lambda hwnd, index, style: styles.append(style) or 1
    win_overlay.make_tool_window(1, no_activate=False)
    win_overlay.make_tool_window(1, no_activate=True)
    assert styles[0] & win_overlay.WS_EX_NOACTIVATE == 0 and styles[0] & win_overlay.WS_EX_TOOLWINDOW
    assert styles[1] & win_overlay.WS_EX_NOACTIVATE and not styles[1] & win_overlay.WS_EX_APPWINDOW


def test_opacity_sets_form_opacity_and_keeps_capture_exclusion(fake_windows):
    affinities = []
    fake_windows.SetWindowDisplayAffinity = lambda hwnd, value: affinities.append(value) or 1
    native = types.SimpleNamespace(Opacity=1.0, InvokeRequired=False)
    controller = OverlayWindow(types.SimpleNamespace(native=native))
    controller.hwnd = 1234
    controller.set_hide_from_share(True)
    affinities.clear()
    controller.set_opacity(0.5)
    assert native.Opacity == 0.5
    assert affinities == [win_overlay.WDA_EXCLUDEFROMCAPTURE]
    controller.set_opacity(0.01)
    assert native.Opacity == 0.3


# --- E: main window hides while the overlay is up ----------------------------------------


def test_main_window_hides_with_overlay_and_returns(tmp_path):
    runtime = _runtime(tmp_path)
    main = FakeMain()
    runtime.attach_overlay(FakeController())
    runtime.attach_main_window(main)

    runtime.set_listening(True)
    assert main.calls == ["hide"]
    runtime.set_listening(False)
    assert main.calls == ["hide", "show"]
    _wait_summary(runtime)

    runtime.set_overlay(visible=True)
    runtime.set_overlay(visible=False)
    assert main.calls[-2:] == ["hide", "show"]

    runtime.set_overlay(visible=True)
    runtime.show_main_window()  # tray 'Show Escort'
    assert main.calls[-1] == "show"


def test_main_window_never_hidden_without_a_working_overlay(tmp_path):
    runtime = _runtime(tmp_path)
    main = FakeMain()
    runtime.attach_main_window(main)
    runtime.set_listening(True)  # no overlay at all
    assert "hide" not in main.calls
    runtime.set_listening(False)
    _wait_summary(runtime)
    main.calls.clear()

    class Broken(FakeController):
        def set_visible(self, visible):
            raise RuntimeError("window gone")

    runtime.attach_overlay(Broken())
    runtime.set_overlay(visible=True)
    assert main.calls == ["hide", "show"], "a failing overlay must leave the main window shown"
    assert runtime._main_hidden is False


# --- F: summary after the meeting --------------------------------------------------------


def _seed_meeting(store: MeetingStore) -> None:
    _add_turn(store, "Them", "Pwede ba natin idagdag yung QR payments sa Phase 1?")
    _add_turn(store, "Me", "I'll send the revised estimate by Friday.")
    _add_card(store, "PRD_CONFLICT", title="QR payments in Phase 1", prd_section="Scope · Phase 1")
    _add_card(store, "COMMITMENT", title="Estimate", who="Me", what="send the revised estimate", when="Friday")


def test_rules_summary_after_stop_without_key(tmp_path):
    runtime = _runtime(tmp_path)
    client = TestClient(create_desktop_app(runtime))
    assert client.get("/api/summary").json()["status"] == "idle"
    client.post("/api/listen", json={"on": True})
    _seed_meeting(runtime.store)
    stopped = client.post("/api/listen", json={"on": False}).json()
    assert stopped["summary"]["status"] in {"generating", "ready"}

    summary = _wait_summary(runtime)
    assert summary["status"] == "ready" and summary["source"] == "rules"
    assert summary["title"] and summary["generated_at"]
    assert summary["commitments"] == [{"who": "Me", "what": "send the revised estimate", "due": "Friday"}]
    assert summary["conflicts"] == [{"title": "QR payments in Phase 1", "section": "Scope · Phase 1"}]
    assert any("QR payments" in bullet for bullet in summary["bullets"])
    assert client.get("/api/status").json()["summary"] == summary == client.get("/api/summary").json()

    transcript = client.get("/api/transcript").json()
    assert [turn["speaker"] for turn in transcript["turns"]] == ["Them", "Me"]

    client.post("/api/meeting/delete")
    assert client.get("/api/summary").json()["status"] == "idle"
    assert client.get("/api/transcript").json()["turns"] == []


def _muse_reply(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def test_muse_summary_with_key_and_fallback_on_failure(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _muse_reply(
            {
                "title": "BayaniPay scope call",
                "bullets": ["Client asked for QR in Phase 1.", "Estimate due Friday."],
                "commitments": [{"who": "Me", "what": "send estimate", "due": "Friday"}],
                "conflicts": [],
            }
        )

    runtime = _runtime(tmp_path, summary_client=httpx.Client(transport=httpx.MockTransport(handler)))
    runtime.secrets.set("meta_api_key", "test-key")
    runtime.set_listening(True)
    _seed_meeting(runtime.store)
    runtime.set_listening(False)
    summary = _wait_summary(runtime)
    assert summary["status"] == "ready" and summary["title"] == "BayaniPay scope call"
    assert summary["bullets"][0] == "Client asked for QR in Phase 1."
    assert summary["commitments"] == [{"who": "Me", "what": "send estimate", "due": "Friday"}]
    assert summary["conflicts"] == [{"title": "QR payments in Phase 1", "section": "Scope · Phase 1"}], "kept from the live cards"
    assert "QR payments sa Phase 1" in seen[0]["messages"][1]["content"]

    failing = _runtime(tmp_path / "b", summary_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    failing.secrets.set("meta_api_key", "test-key")
    _seed_meeting(failing.store)
    failing.regenerate_summary()
    fallback = _wait_summary(failing)
    assert fallback["status"] == "ready" and fallback["source"] == "rules"
    assert "Muse summary failed" in fallback["error"]


def test_summary_regenerate_endpoint_and_stale_results_dropped(tmp_path):
    gate = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        gate.wait(5)
        return _muse_reply({"title": "late", "bullets": ["x"], "commitments": [], "conflicts": []})

    runtime = _runtime(tmp_path, summary_client=httpx.Client(transport=httpx.MockTransport(slow)))
    runtime.secrets.set("meta_api_key", "test-key")
    client = TestClient(create_desktop_app(runtime))
    assert client.post("/api/summary/regenerate").json()["status"] == "generating"
    thread = runtime._summary_thread
    client.post("/api/meeting/delete")
    gate.set()
    thread.join(timeout=5)
    assert runtime.summary_status()["status"] == "idle", "a deleted meeting's summary must not come back"


def test_summary_is_useful_even_for_an_empty_meeting(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.set_listening(True)
    runtime.set_listening(False)
    summary = _wait_summary(runtime)
    assert summary["status"] == "ready"
    assert summary["bullets"] == ["No speech was transcribed in this meeting."]
