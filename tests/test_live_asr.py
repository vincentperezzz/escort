import threading
import time

from escort import live
from escort.settings import load_settings
from escort.store import MeetingStore


class Capture:
    """Me talks steadily; Them (loopback) delivers nothing, like WASAPI when no one is in the call."""

    def speakers(self):
        return ["Me", "Them"]

    def read(self, speaker, block=True):
        if speaker == "Me":
            time.sleep(0.01)
            return b"\x01\x00" * 240
        return b""


class Asr:
    instances: list = []
    die_after: dict = {}

    def __init__(self, settings, speaker, on_final, on_partial=None, keywords=None) -> None:
        self.speaker = speaker
        self.bytes = 0
        self.writes = 0
        self.alive = True
        self.error = None
        Asr.instances.append(self)

    def start(self):
        pass

    def write(self, pcm):
        if not self.alive:
            return False
        self.writes += 1
        self.bytes += len(pcm)
        limit = Asr.die_after.get(self.speaker)
        if limit and self.writes >= limit and sum(1 for i in Asr.instances if i.speaker == self.speaker) == 1:
            self.alive = False
            self.error = "Ingress audio slower than real-time"
        return True

    def close(self):
        self.alive = False


class Pipeline:
    def __init__(self, store):
        self.store = store
        self.prd_text = ""

    def finish_turn(self, turn):
        pass

    def note_partial(self, speaker, text):
        pass


def _run(seconds, monkeypatch, store):
    monkeypatch.setattr(live, "RECONNECT_BACKOFF_S", (0.05,))
    stop = threading.Event()
    thread = threading.Thread(target=live._stream_muse, args=(Capture(), Pipeline(store), load_settings(), stop, Asr), daemon=True)
    thread.start()
    time.sleep(seconds)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_silent_loopback_is_fed_digital_silence_at_real_time(tmp_path, monkeypatch):
    Asr.instances, Asr.die_after = [], {}
    _run(1.5, monkeypatch, MeetingStore(tmp_path))
    them = next(i for i in Asr.instances if i.speaker == "Them")
    seconds_fed = them.bytes / live.PCM_BYTES_PER_S
    assert 0.8 <= seconds_fed <= 1.6  # kept roughly at the wall clock, never ahead of it


def test_a_dropped_stream_reconnects_and_the_other_keeps_going(tmp_path, monkeypatch):
    Asr.instances, Asr.die_after = [], {"Them": 2}
    store = MeetingStore(tmp_path)
    _run(1.6, monkeypatch, store)
    them = [i for i in Asr.instances if i.speaker == "Them"]
    me = [i for i in Asr.instances if i.speaker == "Me"]
    assert len(them) == 2 and them[-1].alive is False  # closed on stop
    assert len(me) == 1 and me[0].writes > 20
    assert any("Them transcription reconnected after: Ingress audio slower than real-time" in w for w in store.warnings)


class Boom(Capture):
    opens = 0

    def __init__(self):
        Boom.opens += 1

    def open(self, mic_index=None, loopback_index=None):
        pass

    def read(self, speaker, block=True):
        if Boom.opens == 1:
            raise RuntimeError("Event loop is closed")
        return super().read(speaker, block)

    def close(self):
        pass


def test_audio_pipeline_restarts_itself_while_still_live(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "RECONNECT_BACKOFF_S", (0.05,))
    monkeypatch.setenv("MODEL_API_KEY", "k")
    Boom.opens, Asr.instances, Asr.die_after = 0, [], {}
    store = MeetingStore(tmp_path)
    stats = live.LiveStats()
    stop = threading.Event()
    pipeline = Pipeline(store)
    pipeline.transcriber = type("T", (), {"name": "muse"})()
    thread = threading.Thread(
        target=live._audio_loop, args=(pipeline, load_settings(), stop, stats), kwargs={"audio_factory": Boom, "session_factory": Asr}, daemon=True
    )
    thread.start()
    time.sleep(0.6)
    assert Boom.opens == 2
    assert stats.audio_running is True
    assert stats.error() is None  # the stale "stopped" message is cleared once it is back
    assert "Live transcription restarted." in store.warnings
    stop.set()
    thread.join(timeout=5)


def test_restart_capture_keeps_the_meeting(tmp_path):
    from escort.desktop.runtime import demo_runtime

    runtime = demo_runtime(tmp_path)
    runtime.set_listening(True)
    try:
        runtime.store.add_utterance(speaker="Them", text="RBAC scope is admin and rep.", start_ms=0, end_ms=0, finished_at=0.0)
        first = runtime.live
        meeting_id = runtime.meeting_id
        runtime.restart_capture()
        assert runtime.live is not first and runtime.listening
        assert runtime.meeting_id == meeting_id
        assert [u.text for u in runtime.store.utterances] == ["RBAC scope is admin and rep."]
    finally:
        runtime.set_listening(False)
