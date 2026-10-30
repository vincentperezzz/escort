from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

from taglish_listener.audio_util import rms_s16, write_wav
from taglish_listener.capture.screen import ScreenGrabber
from taglish_listener.capture.windows_audio import WindowsAudioCapture
from taglish_listener.pipeline import Pipeline
from taglish_listener.prd import keywords_from_prd
from taglish_listener.settings import Settings
from taglish_listener.transcribe.muse import MuseRealtimeSession


def start_live(pipeline: Pipeline, settings: Settings, stop: threading.Event) -> None:
    pipeline.store.mode = "live"
    pipeline.start_interval(settings.spark_interval_s, stop)
    threading.Thread(target=_screen_loop, args=(pipeline, stop), name="screen", daemon=True).start()
    threading.Thread(target=_audio_loop, args=(pipeline, settings, stop), name="audio", daemon=True).start()


def _screen_loop(pipeline: Pipeline, stop: threading.Event) -> None:
    grabber = ScreenGrabber()
    try:
        while not stop.is_set():
            started = time.perf_counter()
            try:
                pipeline.ingest_image(grabber.grab())
            except Exception as exc:
                pipeline.store.warn(f"screen grab failed: {exc}")
                break
            remaining = 1.0 - (time.perf_counter() - started)
            if remaining > 0:
                stop.wait(remaining)
    finally:
        grabber.close()


def _audio_loop(pipeline: Pipeline, settings: Settings, stop: threading.Event) -> None:
    if pipeline.transcriber.name == "scripted-fixture" and not settings.api_key:
        pipeline.store.warn(
            "Live mic capture needs MODEL_API_KEY for Muse Voice Transcribe, "
            "or pip install 'taglish-listener[whisper]' for the open-weights fallback. "
            "Use replay for the staged demo."
        )
        return
    capture = WindowsAudioCapture()
    try:
        capture.open()
    except Exception as exc:
        pipeline.store.warn(str(exc))
        return
    try:
        if settings.api_key:
            _stream_muse(capture, pipeline, settings, stop)
        else:
            _stream_vad(capture, pipeline, stop)
    finally:
        capture.close()


def _stream_muse(capture: WindowsAudioCapture, pipeline: Pipeline, settings: Settings, stop: threading.Event) -> None:
    sessions: dict[str, MuseRealtimeSession] = {}
    keywords = keywords_from_prd(pipeline.prd_text)
    for speaker in capture.speakers():
        session = MuseRealtimeSession(
            settings,
            speaker,
            on_final=pipeline.finish_turn,
            on_partial=pipeline.note_partial,
            keywords=keywords,
        )
        session.start()
        sessions[speaker] = session
    pipeline.store.transcriber = settings.asr_model
    try:
        while not stop.is_set():
            for speaker in capture.speakers():
                sessions[speaker].write(capture.read(speaker))
    finally:
        for session in sessions.values():
            session.close()


def _stream_vad(capture: WindowsAudioCapture, pipeline: Pipeline, stop: threading.Event) -> None:
    buffers = {speaker: bytearray() for speaker in capture.speakers()}
    speaking = {speaker: False for speaker in capture.speakers()}
    silence_ms = {speaker: 0.0 for speaker in capture.speakers()}
    while not stop.is_set():
        for speaker in capture.speakers():
            pcm = capture.read(speaker)
            if not pcm:
                continue
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
