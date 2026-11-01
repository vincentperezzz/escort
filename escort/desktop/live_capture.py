"""Runs escort.live capture for the desktop app on top of its MeetingStore.

LiveSession owns one stop Event and the capture threads. Every failure is
recorded in `error()` (and the store warnings) instead of raising, so the
desktop window and the staged demo keep working without a key or devices.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from escort.capture.windows_audio import parse_device_id
from escort.live import LiveStats, start_live
from escort.pipeline import Pipeline
from escort.prd import keywords_from_prd
from escort.replay import build_segmenter, build_spark, resolve_transcriber_mode
from escort.settings import Settings
from escort.store import MeetingStore

log = logging.getLogger("escort.desktop.live")


def build_live_pipeline(store: MeetingStore, settings: Settings, prd_text: str) -> Pipeline:
    mode = resolve_transcriber_mode("auto", settings)
    if mode == "muse":
        from escort.transcribe.muse import MuseFileTranscriber

        transcriber: Any = MuseFileTranscriber(settings, keywords=keywords_from_prd(prd_text))
    elif mode == "whisper":
        from escort.transcribe.whisper_fallback import WhisperTranscriber

        transcriber = WhisperTranscriber()
    else:
        from escort.transcribe.scripted import ScriptedTranscriber

        transcriber = ScriptedTranscriber({})
    return Pipeline(
        store,
        transcriber,
        build_spark("auto", settings, store),
        build_segmenter("auto", settings, store),
        prd_text,
    )


class LiveSession:
    def __init__(
        self,
        store: MeetingStore,
        settings: Settings,
        prd_text: str,
        *,
        mic_id: object = None,
        loopback_id: object = None,
        on_pipeline: Callable[[], None] | None = None,
        grabber_factory: Callable[[], Any] | None = None,
        audio_factory: Callable[[], Any] | None = None,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.prd_text = prd_text
        self.mic_index = parse_device_id(mic_id)
        self.loopback_index = parse_device_id(loopback_id)
        self.on_pipeline = on_pipeline
        self.grabber_factory = grabber_factory
        self.audio_factory = audio_factory
        self.session_factory = session_factory
        self.stats = LiveStats()
        self.stop_event = threading.Event()
        self.pipeline: Pipeline | None = None
        self.threads: list[threading.Thread] = []

    def start(self) -> LiveSession:
        try:
            self.pipeline = build_live_pipeline(self.store, self.settings, self.prd_text)
            if self.on_pipeline is not None:
                # Pipeline() rewrites store.prd_sections; let the caller restore cited sections.
                self.on_pipeline()
            self.threads = start_live(
                self.pipeline,
                self.settings,
                self.stop_event,
                stats=self.stats,
                mic_index=self.mic_index,
                loopback_index=self.loopback_index,
                grabber_factory=self.grabber_factory,
                audio_factory=self.audio_factory,
                session_factory=self.session_factory,
            )
        except Exception as exc:
            log.warning("Live capture did not start: %s", exc)
            self.stats.fail(f"Live capture did not start: {exc}")
            self.store.warn(f"Live capture did not start: {exc}")
        return self

    def stop(self, timeout: float = 6.0) -> None:
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return not self.stop_event.is_set() and any(thread.is_alive() for thread in self.threads)

    def screen(self) -> dict[str, Any]:
        return self.stats.screen()

    def error(self) -> str | None:
        return self.stats.error()


def start_session(store: MeetingStore, settings: Settings, prd_text: str, **kwargs: Any) -> LiveSession:
    """Seam for tests: conftest replaces this so no real screen or audio is touched."""
    return LiveSession(store, settings, prd_text, **kwargs).start()
