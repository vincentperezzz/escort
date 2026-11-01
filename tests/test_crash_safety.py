"""Native-crash regressions: PortAudio thread safety and the crash log."""

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from escort.capture import windows_audio
from escort.desktop import devices

ROOT = Path(__file__).resolve().parents[1]


class RacyPortAudio:
    """A fake pyaudiowpatch whose global init/terminate/open/close fail the test when entered concurrently.

    Real PortAudio crashed Escort with an access violation when two threads did this at once.
    """

    paWASAPI = 13
    paInt16 = 8

    def __init__(self) -> None:
        self.inside = 0
        self.overlaps = 0
        self.calls = 0
        self.guard = threading.Lock()
        module = self

        class Stream:
            def stop_stream(self) -> None:
                module._critical()

            def close(self) -> None:
                module._critical()

        class PyAudio:
            def __init__(self) -> None:
                module._critical()

            def terminate(self) -> None:
                module._critical()

            def get_host_api_info_by_type(self, _type):
                module._critical()
                return {"index": 0, "defaultInputDevice": 0, "defaultOutputDevice": 1}

            def get_device_count(self) -> int:
                return 2

            def get_device_info_by_index(self, index):
                return [
                    {"name": "Mic", "maxInputChannels": 1, "hostApi": 0, "defaultSampleRate": 48000},
                    {"name": "Speakers [Loopback]", "maxInputChannels": 2, "hostApi": 0, "isLoopbackDevice": True, "defaultSampleRate": 48000},
                ][index]

            def open(self, **_kwargs):
                module._critical()
                return Stream()

        self.PyAudio = PyAudio

    def _critical(self) -> None:
        with self.guard:
            self.inside += 1
            self.calls += 1
            if self.inside > 1:
                self.overlaps += 1
        time.sleep(0.0005)
        with self.guard:
            self.inside -= 1


def test_device_listing_and_capture_never_enter_portaudio_concurrently(monkeypatch):
    fake = RacyPortAudio()
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    stop = threading.Event()
    errors: list[BaseException] = []

    def poll() -> None:
        while not stop.is_set():
            try:
                devices.list_audio_devices(fresh=True, pyaudio_module=fake)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

    def capture() -> None:
        while not stop.is_set():
            try:
                cap = windows_audio.WindowsAudioCapture()
                cap.open()
                cap.close()
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

    threads = [threading.Thread(target=poll) for _ in range(4)] + [threading.Thread(target=capture) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.4)
    stop.set()
    for thread in threads:
        thread.join(5)
    assert not errors
    assert fake.calls > 50
    assert fake.overlaps == 0


def test_device_list_is_cached_between_status_polls(monkeypatch):
    fake = RacyPortAudio()
    monkeypatch.setattr(devices, "_cache", None)
    first = devices.list_audio_devices(pyaudio_module=fake)
    calls = fake.calls
    for _ in range(20):
        assert devices.list_audio_devices(pyaudio_module=fake) == first
    assert fake.calls == calls, "status polls must not re-initialise PortAudio"
    assert {item["kind"] for item in first} == {"mic", "loopback"}
    monkeypatch.setattr(devices, "CACHE_S", 0.0)
    devices.list_audio_devices(pyaudio_module=fake)
    assert fake.calls > calls


def _run(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(code)
    return subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_crash_log_captures_a_native_crash_with_python_stacks(tmp_path: Path):
    _run(
        """
        import ctypes, sys, threading
        from pathlib import Path
        sys.stdout = None
        sys.stderr = None  # the windowed build has neither
        from escort.desktop import crashlog
        crashlog.install(Path(sys.argv[1]))
        print("stray print is not lost")

        def audio_thread():
            ctypes.string_at(0)  # a real access violation on a worker thread

        t = threading.Thread(target=audio_thread, name="escort-audio")
        t.start()
        t.join()
        """,
        tmp_path,
    )
    text = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "stray print is not lost" in text
    assert "fatal exception: access violation" in text or "Segmentation fault" in text, text
    assert "audio_thread" in text, text


def test_crash_log_records_uncaught_thread_exceptions_and_rotates(tmp_path: Path):
    code = """
        import sys, threading
        from pathlib import Path
        sys.stdout = None
        sys.stderr = None
        from escort.desktop import crashlog
        crashlog.install(Path(sys.argv[1]))

        def boom():
            raise RuntimeError("overlay thread blew up")

        t = threading.Thread(target=boom, name="escort-overlay")
        t.start()
        t.join()
        """
    assert _run(code, tmp_path).returncode == 0
    assert _run(code, tmp_path).returncode == 0
    log = (tmp_path / "escort.log").read_text(encoding="utf-8")
    assert "Uncaught exception in thread escort-overlay" in log and "overlay thread blew up" in log
    assert (tmp_path / "crash.1.log").exists(), "the previous run's crash log is kept"


def test_install_is_idempotent(tmp_path: Path, monkeypatch):
    from escort.desktop import crashlog

    monkeypatch.setattr(crashlog, "_state", {"dir": tmp_path})
    assert crashlog.install(tmp_path / "other") == tmp_path
    assert not (tmp_path / "other").exists()


@pytest.mark.parametrize("env, expected", [({"ESCORT_LOG_DIR": "X:/logs"}, Path("X:/logs")), ({"APPDATA": "C:/AppData"}, Path("C:/AppData/Escort/logs"))])
def test_log_dir_location(monkeypatch, env, expected):
    from escort.desktop import crashlog

    monkeypatch.delenv("ESCORT_LOG_DIR", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert crashlog.log_dir() == expected

