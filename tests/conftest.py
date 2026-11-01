import threading

import pytest

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
    return sessions
