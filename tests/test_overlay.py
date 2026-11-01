import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from escort.desktop import hotkey as hotkey_mod
from escort.desktop import launch, win_overlay
from escort.desktop.api import create_desktop_app
from escort.desktop.hotkey import WM_HOTKEY, GlobalHotkey
from escort.desktop.logo import DOT_IDLE, DOT_LIVE, render_logo
from escort.desktop.overlay import OverlayWindow
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore


class FakeController:
    def __init__(self, supported: bool = True) -> None:
        self.supported = supported
        self.calls: list[tuple[str, bool]] = []

    def set_visible(self, visible: bool) -> None:
        self.calls.append(("visible", visible))

    def set_hide_from_share(self, hide: bool) -> None:
        self.calls.append(("hide", hide))


class RecordingUser32:
    """Stands in for user32; records every call and fails the test if hit unexpectedly."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def call(*args):
            self.calls.append(name)
            return 1

        return call


def _runtime(tmp_path: Path) -> DesktopRuntime:
    return DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")


def test_overlay_api_round_trip_with_fake_controller(tmp_path: Path):
    runtime = _runtime(tmp_path)
    fake = FakeController()
    runtime.attach_overlay(fake)
    client = TestClient(create_desktop_app(runtime))

    extra = {"collapsed": False, "opacity": 0.92, "input_focus": False}
    got = client.get("/api/overlay").json()
    assert got == {"hide_from_share": True, "visible": False, "listening": False, "supported": True, **extra}
    assert fake.calls == [("hide", True), ("visible", False)]

    shown = client.post("/api/overlay", json={"visible": True}).json()
    assert shown["visible"] is True and shown["hide_from_share"] is True
    assert fake.calls[-1] == ("visible", True)

    off = client.post("/api/overlay", json={"hide_from_share": False}).json()
    assert off == {"hide_from_share": False, "visible": True, "listening": False, "supported": True, **extra}
    assert fake.calls[-1] == ("hide", False)

    status = client.get("/api/status").json()
    assert status["overlay"] == off


def test_listening_shows_and_hides_the_overlay(tmp_path: Path):
    runtime = _runtime(tmp_path)
    fake = FakeController()
    runtime.attach_overlay(fake)
    client = TestClient(create_desktop_app(runtime))

    on = client.post("/api/listen", json={"on": True}).json()
    assert on["overlay"]["visible"] is True and on["overlay"]["listening"] is True
    off = client.post("/api/listen", json={"on": False}).json()
    assert off["overlay"]["visible"] is False
    assert fake.calls[-2:] == [("visible", True), ("visible", False)]


def test_hide_from_share_defaults_true_and_persists(tmp_path: Path):
    runtime = _runtime(tmp_path)
    assert runtime.hide_from_share is True
    assert runtime.overlay_status()["hide_from_share"] is True

    runtime.set_overlay(hide_from_share=False)
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["hide_from_share"] is False
    assert _runtime(tmp_path).hide_from_share is False

    runtime.set_overlay(hide_from_share=True)
    assert _runtime(tmp_path).hide_from_share is True


def test_overlay_failures_never_break_the_api(tmp_path: Path):
    class Broken(FakeController):
        def set_visible(self, visible: bool) -> None:
            raise RuntimeError("window gone")

    runtime = _runtime(tmp_path)
    runtime.attach_overlay(Broken())
    client = TestClient(create_desktop_app(runtime))
    assert client.post("/api/overlay", json={"visible": True}).json()["visible"] is True
    assert client.post("/api/listen", json={"on": True}).status_code == 200


@pytest.mark.parametrize(("platform", "build"), [("linux", 0), ("darwin", 0), ("win32", 18363)])
def test_unsupported_os_skips_display_affinity(monkeypatch, platform, build):
    fake = RecordingUser32()
    monkeypatch.setattr(win_overlay, "_USER32", fake)
    monkeypatch.setattr(win_overlay, "is_windows", lambda: platform == "win32")
    monkeypatch.setattr(win_overlay, "windows_build", lambda: build)

    assert win_overlay.capture_exclusion_supported() is False
    assert win_overlay.set_capture_excluded(1234, True) is False
    assert win_overlay.set_capture_excluded(1234, False) is False
    assert "SetWindowDisplayAffinity" not in fake.calls

    window = types.SimpleNamespace(native=None, show=lambda: None, hide=lambda: None, move=lambda *a: None)
    controller = OverlayWindow(window)
    controller.hwnd = 1234
    controller.set_hide_from_share(True)
    assert controller.supported is False
    assert "SetWindowDisplayAffinity" not in fake.calls


def test_supported_build_applies_affinity_live(monkeypatch):
    fake = RecordingUser32()
    affinities: list[int] = []
    fake.SetWindowDisplayAffinity = lambda hwnd, value: affinities.append(value) or 1
    monkeypatch.setattr(win_overlay, "_USER32", fake)
    monkeypatch.setattr(win_overlay, "is_windows", lambda: True)
    monkeypatch.setattr(win_overlay, "windows_build", lambda: 22631)

    window = types.SimpleNamespace(native=None)
    controller = OverlayWindow(window)
    controller.hwnd = 1234
    controller.set_hide_from_share(True)
    controller.set_hide_from_share(False)
    assert affinities == [win_overlay.WDA_EXCLUDEFROMCAPTURE, win_overlay.WDA_NONE]


def test_hotkey_fails_soft_off_windows_and_when_taken():
    assert GlobalHotkey(lambda: None, platform="linux").start() is False

    class Taken(RecordingUser32):
        def RegisterHotKey(self, *args):
            return 0

    taken = Taken()
    hk = GlobalHotkey(lambda: None, user32=taken, platform="win32")
    assert hk.start() is False
    assert hk.registered is False
    hk.stop()


def test_hotkey_invokes_callback_and_unregisters(monkeypatch):
    hits: list[int] = []

    class OneShot(RecordingUser32):
        def __init__(self) -> None:
            super().__init__()
            self.delivered = False

        def RegisterHotKey(self, *args):
            return 1

        def GetMessageW(self, ref, *args):
            if self.delivered:
                return 0
            self.delivered = True
            ref._obj.message = WM_HOTKEY
            ref._obj.wParam = hotkey_mod._HOTKEY_ID
            return 1

    user32 = OneShot()
    hk = GlobalHotkey(lambda: hits.append(1), user32=user32, platform="win32")
    hk.start()
    hk._thread.join(timeout=2)
    assert hits == [1]
    assert "UnregisterHotKey" in user32.calls


def test_tray_failure_is_soft(tmp_path: Path, monkeypatch):
    broken = types.ModuleType("pystray")

    def boom(*_args, **_kwargs):
        raise OSError("no notification area")

    broken.Icon = boom
    broken.MenuItem = lambda *a, **k: None
    broken.Menu = type("Menu", (), {"SEPARATOR": None, "__init__": lambda self, *a: None})
    monkeypatch.setitem(sys.modules, "pystray", broken)
    assert launch._tray(_runtime(tmp_path), window=None) is None


def test_tray_refresh_is_serialized_and_only_swaps_icon_on_change(tmp_path: Path, monkeypatch):
    import threading
    import time

    state = {"inside": 0, "overlaps": 0, "icon_sets": 0}

    class Icon:
        def __init__(self, *_args, **_kwargs) -> None:
            self.title = ""

        def _critical(self) -> None:
            state["inside"] += 1
            if state["inside"] > 1:
                state["overlaps"] += 1
            time.sleep(0.001)
            state["inside"] -= 1

        @property
        def icon(self):
            return None

        @icon.setter
        def icon(self, _value) -> None:
            state["icon_sets"] += 1
            self._critical()

        def update_menu(self) -> None:
            self._critical()

        def run(self) -> None:
            pass

    fake = types.ModuleType("pystray")
    fake.Icon = Icon
    fake.MenuItem = lambda *a, **k: None
    fake.Menu = type("Menu", (), {"SEPARATOR": None, "__init__": lambda self, *a: None})
    monkeypatch.setitem(sys.modules, "pystray", fake)
    runtime = _runtime(tmp_path)
    assert launch._tray(runtime, window=None) is not None
    threads = [threading.Thread(target=runtime._notify) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert state["overlaps"] == 0
    assert state["icon_sets"] == 1, "the icon image is only replaced when listening changes"


def test_hotkey_helper_is_soft(tmp_path: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("ctypes missing")

    monkeypatch.setattr(launch, "GlobalHotkey", boom)
    assert launch._hotkey(_runtime(tmp_path)) is None


def test_logo_colors_and_sizes():
    idle, live = render_logo(64), render_logo(64, live=True)
    assert idle.size == (64, 64) and idle.mode == "RGBA"
    hex_rgb = lambda value: tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    assert idle.getpixel((47, 32))[:3] == hex_rgb(DOT_IDLE)
    assert live.getpixel((47, 32))[:3] == hex_rgb(DOT_LIVE)
    assert render_logo(16).size == (16, 16)
