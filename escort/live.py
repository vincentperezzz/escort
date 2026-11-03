"""Live capture: screen frames + Me/Them audio feeding a Pipeline until `stop` is set.

The screen loop samples one display about once a second. Which display comes from
`monitor_source` (re-read every tick, so a change in Settings or chat applies live);
on a switch the grabber is pointed at the new mss index and the change detector's
baseline is reset, so the first frame of the new display is kept. Every grab goes
through Pipeline.ingest_image, whose FrameGate (escort/diff.py) works like a video
encoder's keyframe logic: each grab is shrunk to a blurred 384x216 grey copy and
compared with the last *kept* frame per pixel and per 12x12 tile. Repeats and cursor
moves (too few changed pixels/tiles) are never resent; a real change is held until
the next grab matches it (the screen settled), so a scroll or fade yields one frame;
a screen that never settles (video) yields at most one frame every 8 s. Skipped
grabs are only counted. The store keeps just the newest kept frame.

Every loop fails soft: a missing dependency, device or key is reported through
`LiveStats.fail` / store warnings and never raises out of the thread.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from escort.audio_util import rms_s16, write_wav
from escort.pipeline import Pipeline
from escort.prd import keywords_from_prd
from escort.settings import Settings
from escort.store import utc_now

SCREEN_INTERVAL_S = 1.0
IDLE_WAIT_S = 0.01
PCM_BYTES_PER_S = 24000 * 2  # captures hand over 24 kHz mono s16
SILENCE_FILL_AFTER_S = 0.5  # a stream quiet this long gets digital silence, so ASR never starves
RECONNECT_BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)
AUDIO_RESTARTS = 6  # whole-pipeline restarts in a row before giving up (a run of 60 s+ resets the count)
AUDIO_STABLE_S = 60.0
TRANSCRIPTION_STOPPED = "Live transcription stopped"


class LiveStats:
    """Thread-safe counters the desktop status endpoint exposes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.monitoring = False
        self.last_frame_at: str | None = None
        self.frames_seen = 0
        self.changed_frames = 0
        self.audio_running = False
        self.monitor: str | None = None
        self.errors: list[str] = []

    def set_monitor(self, label: str | None) -> None:
        with self._lock:
            self.monitor = label

    def frame(self, changed: bool) -> None:
        with self._lock:
            self.frames_seen += 1
            self.last_frame_at = utc_now()
            if changed:
                self.changed_frames += 1

    def fail(self, message: str) -> None:
        with self._lock:
            if message not in self.errors:
                self.errors.append(message)

    def clear(self, prefix: str) -> None:
        """Drop errors that no longer hold (e.g. transcription came back after a restart)."""
        with self._lock:
            self.errors = [item for item in self.errors if not item.startswith(prefix)]

    def screen(self) -> dict[str, Any]:
        with self._lock:
            return {
                "monitoring": self.monitoring,
                "last_frame_at": self.last_frame_at,
                "frames_seen": self.frames_seen,
                "changed_frames": self.changed_frames,
                "monitor": self.monitor,
            }

    def error(self) -> str | None:
        with self._lock:
            return "; ".join(self.errors) or None


def _default_grabber():
    from escort.capture.screen import ScreenGrabber

    return ScreenGrabber()


def _default_audio():
    from escort.capture.windows_audio import WindowsAudioCapture

    return WindowsAudioCapture()


def start_live(
    pipeline: Pipeline,
    settings: Settings,
    stop: threading.Event,
    *,
    stats: LiveStats | None = None,
    mic_index: int | None = None,
    loopback_index: int | None = None,
    grabber_factory: Callable[[], Any] | None = None,
    audio_factory: Callable[[], Any] | None = None,
    session_factory: Callable[..., Any] | None = None,
    monitor_source: Callable[[], dict[str, Any]] | None = None,
) -> list[threading.Thread]:
    stats = stats or LiveStats()
    pipeline.store.mode = "live"
    pipeline.start_interval(settings.spark_interval_s, stop)
    threads = [
        threading.Thread(
            target=_screen_loop,
            args=(pipeline, stop, stats, grabber_factory or _default_grabber),
            kwargs={"monitor_source": monitor_source},
            name="screen",
            daemon=True,
        ),
        threading.Thread(
            target=_audio_loop,
            args=(pipeline, settings, stop, stats),
            kwargs={
                "mic_index": mic_index,
                "loopback_index": loopback_index,
                "audio_factory": audio_factory or _default_audio,
                "session_factory": session_factory,
            },
            name="audio",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    return threads


def _screen_loop(
    pipeline: Pipeline,
    stop: threading.Event,
    stats: LiveStats | None = None,
    grabber_factory: Callable[[], Any] | None = None,
    *,
    monitor_source: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """Grab ~1 frame/s. `monitor_source()` returns {"index": mss index, "label": str}."""
    stats = stats or LiveStats()
    try:
        grabber = (grabber_factory or _default_grabber)()
    except Exception as exc:
        message = f"Screen capture unavailable: {exc}"
        stats.fail(message)
        pipeline.store.warn(message)
        return
    stats.monitoring = True
    current: int | None = None
    try:
        while not stop.is_set():
            started = time.perf_counter()
            if monitor_source is not None:
                current = _apply_monitor(grabber, pipeline, stats, monitor_source, current)
            try:
                changed = pipeline.ingest_image(grabber.grab())
            except Exception as exc:
                message = f"Screen grab failed: {exc}"
                stats.fail(message)
                pipeline.store.warn(message)
                break
            stats.frame(bool(changed))
            remaining = SCREEN_INTERVAL_S - (time.perf_counter() - started)
            if remaining > 0:
                stop.wait(remaining)
    finally:
        stats.monitoring = False
        try:
            grabber.close()
        except Exception:
            pass


def _apply_monitor(
    grabber: Any,
    pipeline: Pipeline,
    stats: LiveStats,
    monitor_source: Callable[[], dict[str, Any]],
    current: int | None,
) -> int | None:
    """Point the grabber at the chosen display; on a switch, reset the change baseline."""
    try:
        target = monitor_source()
        index = int(target["index"])
    except Exception:
        return current  # keep watching what we watch; a bad read never stops the loop
    stats.set_monitor(target.get("label"))
    if index == current:
        return current
    setter = getattr(grabber, "set_monitor", None)
    if setter is not None:
        setter(index)
    if current is not None:
        pipeline.gate.reset()
    return index


def _audio_loop(
    pipeline: Pipeline,
    settings: Settings,
    stop: threading.Event,
    stats: LiveStats | None = None,
    *,
    mic_index: int | None = None,
    loopback_index: int | None = None,
    audio_factory: Callable[[], Any] | None = None,
    session_factory: Callable[..., Any] | None = None,
) -> None:
    stats = stats or LiveStats()

    def fail(message: str) -> None:
        stats.fail(message)
        pipeline.store.warn(message)

    if pipeline.transcriber.name == "scripted-fixture" and not settings.api_key:
        fail(
            "Live transcription needs the Meta API key (Setup) for Muse Voice Transcribe, "
            "or pip install 'escort[whisper]' for the open-weights fallback. Screen monitoring still runs."
        )
        return
    restarts = 0
    while not stop.is_set():
        try:
            capture = (audio_factory or _default_audio)()
            capture.open(mic_index=mic_index, loopback_index=loopback_index)
        except Exception as exc:
            if restarts == 0:
                fail(f"Audio capture unavailable: {exc}")
                return
            capture = None
            error: Exception | None = exc
        else:
            error = None
        if capture is not None:
            stats.audio_running = True
            if restarts:
                stats.clear(TRANSCRIPTION_STOPPED)
                pipeline.store.warn("Live transcription restarted.")
            began = time.monotonic()
            try:
                if settings.api_key:
                    _stream_muse(capture, pipeline, settings, stop, session_factory)
                else:
                    _stream_vad(capture, pipeline, stop)
            except Exception as exc:
                error = exc
            finally:
                stats.audio_running = False
                try:
                    capture.close()
                except Exception:
                    pass
            if time.monotonic() - began >= AUDIO_STABLE_S:
                restarts = 0
        if stop.is_set() or error is None:
            return
        # Still live: the meeting keeps going, so transcription comes back on its own.
        restarts += 1
        stats.clear(TRANSCRIPTION_STOPPED)  # one current message, not a pile of old ones
        if restarts > AUDIO_RESTARTS:
            fail(f"{TRANSCRIPTION_STOPPED}: {error}. Use Restart transcription to try again.")
            return
        wait = RECONNECT_BACKOFF_S[min(restarts - 1, len(RECONNECT_BACKOFF_S) - 1)]
        fail(f"{TRANSCRIPTION_STOPPED}: {error} (restarting in {wait:.0f}s)")
        stop.wait(wait)


def _read(capture: Any, speaker: str) -> bytes:
    """Non-blocking read when the capture supports it (WASAPI loopback stalls in silence)."""
    try:
        return capture.read(speaker, block=False)
    except TypeError:
        return capture.read(speaker)


def _stream_muse(
    capture: Any,
    pipeline: Pipeline,
    settings: Settings,
    stop: threading.Event,
    session_factory: Callable[..., Any] | None = None,
) -> None:
    if session_factory is None:
        from escort.transcribe.muse import MuseRealtimeSession

        session_factory = MuseRealtimeSession
    sessions: dict[str, Any] = {}
    keywords = keywords_from_prd(pipeline.prd_text)
    # Per stream: when the session (re)started, bytes fed since, last real audio, reconnect state.
    started: dict[str, float] = {}
    fed: dict[str, int] = {}
    heard: dict[str, float] = {}
    retry_at: dict[str, float] = {}
    failures: dict[str, int] = {}

    def open_session(speaker: str) -> None:
        session = session_factory(
            settings,
            speaker,
            on_final=pipeline.finish_turn,
            on_partial=pipeline.note_partial,
            keywords=keywords,
        )
        session.start()
        sessions[speaker] = session
        now = time.monotonic()
        started[speaker], fed[speaker], heard[speaker] = now, 0, now

    def revive(speaker: str) -> None:
        """The server dropped this stream: reconnect with backoff; the other stream keeps going."""
        now = time.monotonic()
        if now < retry_at.get(speaker, 0.0):
            return
        reason = getattr(sessions.get(speaker), "error", None)
        try:
            open_session(speaker)
        except Exception as exc:
            count = failures.get(speaker, 0)
            failures[speaker] = count + 1
            retry_at[speaker] = now + RECONNECT_BACKOFF_S[min(count, len(RECONNECT_BACKOFF_S) - 1)]
            pipeline.store.warn(f"Reconnecting {speaker} transcription: {exc}")
            return
        failures[speaker] = 0
        pipeline.store.warn(f"{speaker} transcription reconnected" + (f" after: {reason}" if reason else "."))

    try:
        for speaker in capture.speakers():
            open_session(speaker)
        pipeline.store.transcriber = settings.asr_model
        while not stop.is_set():
            got = False
            for speaker in capture.speakers():
                pcm = _read(capture, speaker)
                session = sessions[speaker]
                if not getattr(session, "alive", True):
                    revive(speaker)
                    continue
                now = time.monotonic()
                if pcm:
                    got = True
                    heard[speaker] = now
                    if session.write(pcm) is not False:
                        fed[speaker] += len(pcm)
                elif now - heard[speaker] >= SILENCE_FILL_AFTER_S:
                    # WASAPI loopback delivers nothing while nothing plays (no one in the call yet).
                    behind = int((now - started[speaker]) * PCM_BYTES_PER_S) - fed[speaker]
                    if behind >= PCM_BYTES_PER_S // 10:
                        chunk = min(behind, PCM_BYTES_PER_S) & ~1
                        if session.write(bytes(chunk)) is not False:
                            fed[speaker] += chunk
            if not got:
                stop.wait(IDLE_WAIT_S)
    finally:
        for session in sessions.values():
            try:
                session.close()
            except Exception:
                pass


def _stream_vad(capture: Any, pipeline: Pipeline, stop: threading.Event) -> None:
    buffers = {speaker: bytearray() for speaker in capture.speakers()}
    speaking = {speaker: False for speaker in capture.speakers()}
    silence_ms = {speaker: 0.0 for speaker in capture.speakers()}
    while not stop.is_set():
        got = False
        for speaker in capture.speakers():
            pcm = _read(capture, speaker)
            if not pcm:
                continue
            got = True
            level = rms_s16(pcm)
            duration = (len(pcm) / 2) / 24000 * 1000
            if level >= 800:
                speaking[speaker] = True
                silence_ms[speaker] = 0
                buffers[speaker].extend(pcm)
                continue
            if not speaking[speaker]:
                continue
            buffers[speaker].extend(pcm)
            silence_ms[speaker] += duration
            if silence_ms[speaker] < 500:
                continue
            speech_ms = (len(buffers[speaker]) / 2) / 24000 * 1000
            blob = bytes(buffers[speaker])
            buffers[speaker].clear()
            speaking[speaker] = False
            silence_ms[speaker] = 0
            if speech_ms < 400:
                continue
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / f"{speaker}.wav"
                write_wav(path, blob)
                pipeline.ingest_wav(path, speaker=speaker)
        if not got:
            stop.wait(IDLE_WAIT_S)
