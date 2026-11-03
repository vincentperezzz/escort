import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from escort.desktop import launch, overlay_process
from escort.desktop.overlay_process import (
    CREATE_NO_WINDOW,
    DEV_RELATIVE,
    ElectronOverlay,
    find_overlay_exe,
    start_electron_overlay,
)
from escort.desktop.runtime import DesktopRuntime
from escort.desktop.secret_store import MemorySecretStore
from escort.store import MeetingStore


class FakeProc:
    _next_pid = 4000

    def __init__(self, exit_code: int | None = None) -> None:
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.returncode = exit_code
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("EscortOverlay.exe", timeout)
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


class FakePopen:
    """Hands out FakeProcs; `exit_codes` gives each spawn's state (None = keeps running)."""

    def __init__(self, exit_codes=None, error: Exception | None = None) -> None:
        self.exit_codes = list(exit_codes or [])
        self.error = error
        self.calls: list[tuple[list[str], dict]] = []
        self.procs: list[FakeProc] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.error is not None:
            raise self.error
        proc = FakeProc(self.exit_codes.pop(0) if self.exit_codes else None)
        self.procs.append(proc)
        return proc


class Killer:
    def __init__(self, popen: FakePopen) -> None:
        self.popen = popen
        self.pids: list[int] = []

    def __call__(self, pid: int) -> None:
        self.pids.append(pid)
        for proc in self.popen.procs:
            if proc.pid == pid:
                proc.returncode = 1


def _overlay(tmp_path: Path, popen: FakePopen, **kwargs) -> ElectronOverlay:
    defaults = dict(startup_grace=0.01, watch_interval=0.01, restart_delay=0, kill_tree=Killer(popen))
    defaults.update(kwargs)
    return ElectronOverlay(tmp_path / "EscortOverlay.exe", "http://127.0.0.1:8791", 1234, popen=popen, **defaults)


def _runtime(tmp_path: Path) -> DesktopRuntime:
    return DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


# --- finding the exe ------------------------------------------------------------


def test_finds_the_exe_in_the_dev_layout(tmp_path: Path):
    assert find_overlay_exe(frozen=False, root=tmp_path) is None
    exe = _touch(tmp_path / DEV_RELATIVE)
    assert find_overlay_exe(frozen=False, root=tmp_path) == exe
    assert DEV_RELATIVE.as_posix() == "overlay/out/EscortOverlay-win32-x64/EscortOverlay.exe"


def test_finds_the_exe_next_to_the_installed_escort_exe(tmp_path: Path):
    escort = _touch(tmp_path / "Escort" / "Escort.exe")
    assert find_overlay_exe(frozen=True, executable=str(escort)) is None
    exe = _touch(tmp_path / "Escort" / "overlay" / "EscortOverlay.exe")
    assert find_overlay_exe(frozen=True, executable=str(escort)) == exe.resolve()
    # The dev build in the repo is never picked up by the installed app.
    _touch(tmp_path / DEV_RELATIVE)
    assert find_overlay_exe(frozen=True, executable=str(escort), root=tmp_path) == exe.resolve()


# --- launching --------------------------------------------------------------------


def test_command_line_uses_the_equals_form_with_port_and_pid(tmp_path: Path):
    popen = FakePopen()
    overlay = _overlay(tmp_path, popen)
    assert overlay.start() is True
    try:
        cmd, kwargs = popen.calls[0]
        assert cmd == [str(tmp_path / "EscortOverlay.exe"), "--url=http://127.0.0.1:8791", "--parent-pid=1234"]
        if sys.platform == "win32":
            assert kwargs["creationflags"] & CREATE_NO_WINDOW
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert overlay.is_running()
    finally:
        overlay.stop()


def test_parent_pid_defaults_to_this_process(tmp_path: Path):
    import os

    overlay = ElectronOverlay(tmp_path / "x.exe", "http://127.0.0.1:9000/")
    assert overlay.command()[1:] == ["--url=http://127.0.0.1:9000", f"--parent-pid={os.getpid()}"]


def test_start_fails_when_the_process_exits_during_the_grace_period(tmp_path: Path):
    overlay = _overlay(tmp_path, FakePopen(exit_codes=[-1]))
    assert overlay.start() is False
    assert not overlay.is_running()


def test_start_fails_when_popen_raises(tmp_path: Path):
    overlay = _overlay(tmp_path, FakePopen(error=OSError("not a valid Win32 application")))
    assert overlay.start() is False


def test_restarts_at_most_three_times_then_gives_up(tmp_path: Path):
    popen = FakePopen(exit_codes=[None, 1, 1, 1, 1, 1])
    gone = threading.Event()
    overlay = _overlay(tmp_path, popen, on_gone=gone.set)
    assert overlay.start() is True
    popen.procs[0].returncode = 1  # dies unexpectedly
    assert gone.wait(5)
    assert overlay.restarts == 3
    assert len(popen.calls) == 4  # first start + 3 restarts
    assert not overlay.is_running()
    overlay.stop()


def test_a_restart_that_stays_up_is_kept(tmp_path: Path):
    popen = FakePopen(exit_codes=[None, None])
    overlay = _overlay(tmp_path, popen)
    assert overlay.start() is True
    popen.procs[0].returncode = 1
    for _ in range(500):
        if len(popen.calls) == 2 and overlay.is_running():
            break
        threading.Event().wait(0.01)
    assert len(popen.calls) == 2 and overlay.is_running() and overlay.restarts == 1
    overlay.stop()


def test_stop_kills_the_process_tree_and_never_restarts(tmp_path: Path):
    popen = FakePopen()
    overlay = _overlay(tmp_path, popen)
    assert overlay.start() is True
    pid = popen.procs[0].pid
    overlay.stop()
    assert overlay._kill_tree.pids == [pid]
    assert not overlay.is_running()
    threading.Event().wait(0.1)
    assert len(popen.calls) == 1
    overlay.stop()  # idempotent
    assert overlay._kill_tree.pids == [pid]


def test_runtime_shutdown_stops_the_overlay_process(tmp_path: Path):
    popen = FakePopen()
    overlay = _overlay(tmp_path, popen)
    overlay.start()
    runtime = _runtime(tmp_path)
    runtime.attach_overlay_process(overlay)
    runtime.shutdown()
    assert popen.procs[0].returncode is not None
    assert not overlay.is_running()


def test_start_electron_overlay_is_none_when_the_exe_is_missing(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(overlay_process.DISABLE_ENV, raising=False)
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: None)
    assert start_electron_overlay("http://127.0.0.1:8791") is None


def test_start_electron_overlay_is_none_when_it_fails_to_start(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(overlay_process.DISABLE_ENV, raising=False)
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: tmp_path / "EscortOverlay.exe")
    popen = FakePopen(exit_codes=[-1])
    assert start_electron_overlay("http://127.0.0.1:8791", 1, popen=popen, startup_grace=0.01) is None
    assert len(popen.calls) == 1


def test_start_electron_overlay_can_be_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(overlay_process.DISABLE_ENV, "pywebview")
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: pytest.fail("should not look"))
    assert start_electron_overlay("http://127.0.0.1:8791") is None


# --- runtime: main window ---------------------------------------------------------


class FakeMain:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show(self) -> None:
        self.calls.append("show")

    def hide(self) -> None:
        self.calls.append("hide")


class FakeProcess:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.stopped = False

    def is_running(self) -> bool:
        return self.running

    def stop(self) -> None:
        self.stopped = True
        self.running = False


def test_main_window_hides_while_the_overlay_process_runs(tmp_path: Path):
    runtime = _runtime(tmp_path)
    main = FakeMain()
    runtime.attach_main_window(main)
    runtime.attach_overlay_process(FakeProcess(running=True))
    assert runtime.overlay is None
    runtime.set_listening(True)
    assert runtime.overlay_status()["visible"] is True
    assert main.calls == ["hide"]
    runtime.set_listening(False)
    assert runtime.overlay_status()["visible"] is False
    assert main.calls == ["hide", "show"]


def test_main_window_is_never_hidden_when_the_overlay_process_is_not_running(tmp_path: Path):
    runtime = _runtime(tmp_path)
    main = FakeMain()
    runtime.attach_main_window(main)
    runtime.attach_overlay_process(FakeProcess(running=False))
    runtime.set_listening(True)
    runtime.set_overlay(visible=True)
    assert "hide" not in main.calls
    assert runtime.overlay_status()["visible"] is True  # the flag is still stored


def test_main_window_returns_when_the_overlay_process_is_lost(tmp_path: Path):
    runtime = _runtime(tmp_path)
    main = FakeMain()
    process = FakeProcess(running=True)
    runtime.attach_main_window(main)
    runtime.attach_overlay_process(process)
    runtime.set_listening(True)
    process.running = False
    runtime.overlay_process_lost()
    assert main.calls == ["hide", "show"]


def test_tray_toggles_only_store_flags_in_electron_mode(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.attach_overlay_process(FakeProcess(running=True))
    runtime.toggle_overlay()
    assert runtime.overlay_status()["visible"] is True
    runtime.set_overlay(hide_from_share=False)
    assert runtime.overlay_status()["hide_from_share"] is False
    runtime.toggle_overlay()
    assert runtime.overlay_status()["visible"] is False


# --- launch wiring ------------------------------------------------------------------


class FakeWindow:
    def __init__(self) -> None:
        self.events = types.SimpleNamespace(closed=_Event())

    def show(self):
        pass

    def hide(self):
        pass


class _Event(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


def _fake_webview(monkeypatch) -> types.SimpleNamespace:
    module = types.SimpleNamespace(create_window=lambda *a, **k: FakeWindow(), start=lambda: None)
    monkeypatch.setitem(sys.modules, "webview", module)
    return module


def test_open_window_uses_electron_and_skips_pywebview_overlay_and_hotkey(monkeypatch, tmp_path: Path):
    _fake_webview(monkeypatch)
    started: list[tuple] = []
    electron = FakeProcess(running=True)

    def fake_start(url, parent_pid, on_gone=None):
        started.append((url, parent_pid, on_gone))
        return electron

    monkeypatch.setattr(launch, "start_electron_overlay", fake_start)
    monkeypatch.setattr(launch, "create_overlay", lambda *a: pytest.fail("pywebview overlay must not be created"))
    monkeypatch.setattr(launch, "_hotkey", lambda runtime: pytest.fail("backend hotkey must not be registered"))
    monkeypatch.setattr(launch, "_tray", lambda runtime, window: None)
    runtime = _runtime(tmp_path)

    launch._open_window("http://127.0.0.1:8791", runtime)

    import os

    assert started[0][:2] == ("http://127.0.0.1:8791", os.getpid())
    assert started[0][2] == runtime.overlay_process_lost
    assert runtime.overlay is None and runtime.overlay_process is electron
    assert electron.stopped  # runtime.shutdown() on exit


def test_open_window_falls_back_to_pywebview_overlay_and_hotkey(monkeypatch, tmp_path: Path):
    _fake_webview(monkeypatch)
    pywebview_overlay = types.SimpleNamespace(supported=True, set_visible=lambda v: None, set_hide_from_share=lambda v: None, destroy=lambda: None)
    hotkeys: list[DesktopRuntime] = []
    monkeypatch.setattr(launch, "start_electron_overlay", lambda url, pid, on_gone=None: None)
    monkeypatch.setattr(launch, "create_overlay", lambda webview, url: pywebview_overlay)
    monkeypatch.setattr(launch, "_hotkey", lambda runtime: hotkeys.append(runtime))
    monkeypatch.setattr(launch, "_tray", lambda runtime, window: None)
    runtime = _runtime(tmp_path)

    launch._open_window("http://127.0.0.1:8791", runtime)

    assert runtime.overlay is pywebview_overlay and runtime.overlay_process is None
    assert hotkeys == [runtime]


def test_overlay_url_uses_loopback_for_wildcard_hosts():
    assert launch.overlay_url("http://0.0.0.0:8791") == "http://127.0.0.1:8791"
    assert launch.overlay_url("http://127.0.0.1:8791") == "http://127.0.0.1:8791"


def test_electron_overlay_window_focus_and_mouse_flags():
    root = Path(__file__).resolve().parents[1]
    main = (root / "overlay" / "main.js").read_text(encoding="utf-8")
    assert "focusable: false," in main
    assert "win.setFocusable(true)" in main
    assert "win.setFocusable(false)" in main
    assert "win.setIgnoreMouseEvents(false)" in main
    assert "transparent: true" in main
    assert 'backgroundColor: "#00000000"' in main
