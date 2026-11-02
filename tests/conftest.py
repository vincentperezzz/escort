import threading

import pytest

from escort.capture import monitors
from escort.desktop import live_capture
from escort.live import LiveStats


class FakeLiveSession:
    """Stands in for LiveSession: records how it was started, touches no screen or audio."""

    def __init__(self, store, settings, prd_text, **kwargs) -> None:
        self.store = store
        self.settings = settings
        self.prd_text = prd_text
        self.kwargs = kwargs
        self.stats = LiveStats()
        self.stopped = threading.Event()
        self.stop_calls = 0

    def start(self) -> "FakeLiveSession":
        self.stats.monitoring = True
        return self

    def stop(self, timeout: float = 6.0) -> None:
        self.stop_calls += 1
        self.stats.monitoring = False
        self.stopped.set()

    def screen(self):
        return self.stats.screen()

    def error(self):
        return self.stats.error()


@pytest.fixture(autouse=True)
def no_real_capture_or_keys(monkeypatch):
    """No test may open real devices, grab the screen, or call Muse with a stray key."""
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    sessions: list[FakeLiveSession] = []

    def start_session(store, settings, prd_text, **kwargs):
        session = FakeLiveSession(store, settings, prd_text, **kwargs).start()
        sessions.append(session)
        return session

    monkeypatch.setattr(live_capture, "start_session", start_session)
    # One 1920x1080 display unless a test says otherwise: never enumerate the real screens.
    monkeypatch.setattr(monitors, "enumerate_raw", lambda: [dict(ONE_DISPLAY), dict(ONE_DISPLAY)])
    return sessions


ONE_DISPLAY = {"left": 0, "top": 0, "width": 1920, "height": 1080}


@pytest.fixture(autouse=True)
def _no_user_google_client(monkeypatch, tmp_path_factory):
    """Never read a developer's real google_client.json (per-user or in the repo root) during tests."""
    from escort.context.sources import google_auth

    monkeypatch.delenv("ESCORT_GOOGLE_API_KEY", raising=False)
    missing = tmp_path_factory.mktemp("no-user-client") / "google_client.json"
    monkeypatch.setattr(google_auth, "user_client_path", lambda: missing)
    monkeypatch.setattr(google_auth, "bundled_client_path", lambda: missing)
