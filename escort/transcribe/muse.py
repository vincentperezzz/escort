from __future__ import annotations

import asyncio
import json
import threading
import uuid
import wave
from pathlib import Path

import httpx

from escort.models import Turn
from escort.settings import Settings
from escort.transcribe.base import TranscriberUnavailable
from escort.audio_util import pcm_to_mono_24k
from escort.transcribe.events import (
    AsrReducer,
    asr_urls,
    end_stream_message,
    file_request,
    handshake_frame,
    turns_from_file_payload,
    turns_from_sse,
)
from escort.transcribe.pace import iter_pcm_chunks, pcm_realtime_seconds


class MuseFileTranscriber:
    name = "muse-voice-transcribe-1.0"

    def __init__(self, settings: Settings, client: httpx.Client | None = None, keywords: list[str] | None = None) -> None:
        if not settings.api_key:
            raise TranscriberUnavailable("MODEL_API_KEY is not set")
        self.settings = settings
        self.keywords = keywords or []
        self.name = settings.asr_model
        self._client = client
        self._owns_client = client is None

    def transcribe_file(self, path: Path, *, speaker: str) -> list[Turn]:
        if self._client is None:
            try:
                streamed = asyncio.run(self._stream_file(path, speaker))
                if streamed:
                    return streamed
            except Exception:
                pass
        return self._post_file(path, speaker)

    def _post_file(self, path: Path, speaker: str) -> list[Turn]:
        http_url, _ = asr_urls(self.settings.base_url)
        request = file_request(
            model=self.settings.asr_model,
            mode="ENDPOINTING",
            language_bias=list(self.settings.language_bias),
            keywords=self.keywords,
        )
        session_id = f"file-{uuid.uuid4().hex[:12]}"
        audio = path.read_bytes()
        client = self._client or httpx.Client(timeout=120)
        try:
            response = client.post(
                http_url,
                params={"sessionId": session_id},
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Accept": "application/json, text/event-stream",
                },
                files={
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": (path.name, audio, "audio/wav"),
                },
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return turns_from_sse(response.text, speaker)
            return turns_from_file_payload(response.json(), speaker)
        finally:
            if self._owns_client and self._client is None:
                client.close()

    async def _stream_file(self, path: Path, speaker: str) -> list[Turn]:
        import websockets

        _, ws_base = asr_urls(self.settings.base_url)
        session_id = f"file-{speaker.lower()}-{uuid.uuid4().hex[:12]}"
        uri = f"{ws_base}?sessionId={session_id}"
        frame = handshake_frame(
            self.settings.api_key or "",
            model=self.settings.asr_model,
            mode="ENDPOINTING",
            language_bias=list(self.settings.language_bias),
            keywords=self.keywords,
        )
        pcm = wav_pcm_24k(path)
        turns: list[Turn] = []
        reducer = AsrReducer(speaker)
        async with websockets.connect(uri, open_timeout=8, max_size=8_000_000) as ws:
            await ws.send(json.dumps(frame))
            ack = json.loads(await ws.recv())
            if "sessionId" not in ack:
                raise RuntimeError(f"handshake failed: {ack}")
            for piece in iter_pcm_chunks(pcm):
                await ws.send(piece)
                await asyncio.sleep(pcm_realtime_seconds(len(piece)))
            await ws.send(json.dumps(end_stream_message()))
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                turn = reducer.apply(event)
                if turn and turn.text.strip():
                    turns.append(turn)
        return turns


_DEAD = object()  # queued by the receiver when the socket is gone, so the sender stops too


class MuseRealtimeSession:
    """One streaming ASR socket on its own thread and event loop.

    The server may drop the socket (e.g. "Ingress audio slower than real-time"). Then `alive` turns
    False and `write` becomes a no-op instead of raising "Event loop is closed"; the caller reconnects.
    """

    def __init__(
        self,
        settings: Settings,
        speaker: str,
        on_final,
        on_partial=None,
        keywords: list[str] | None = None,
    ) -> None:
        if not settings.api_key:
            raise TranscriberUnavailable("MODEL_API_KEY is not set")
        self.settings = settings
        self.speaker = speaker
        self.on_final = on_final
        self.on_partial = on_partial
        self.keywords = keywords or []
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread = threading.Thread(target=self._run, name=f"asr-{speaker}", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self.ready.wait(timeout=30):
            raise TimeoutError("Muse realtime handshake timed out")
        if self.error:
            raise self.error

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and self._loop is not None and not self._loop.is_closed()

    def write(self, pcm: bytes) -> bool:
        if not self.alive:
            return False
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, pcm)
        except RuntimeError:  # the loop closed between the check and the call
            return False
        return True

    def close(self) -> None:
        if self.alive:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            except RuntimeError:
                pass
        self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error = exc
            self.ready.set()

    async def _main(self) -> None:
        import websockets

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        _, ws_base = asr_urls(self.settings.base_url)
        session_id = f"{self.speaker.lower()}-{uuid.uuid4().hex[:12]}"
        uri = f"{ws_base}?sessionId={session_id}"
        frame = handshake_frame(
            self.settings.api_key or "",
            model=self.settings.asr_model,
            mode="ENDPOINTING",
            language_bias=list(self.settings.language_bias),
            keywords=self.keywords,
        )
        async with websockets.connect(uri, open_timeout=30, max_size=8_000_000) as ws:
            await ws.send(json.dumps(frame))
            ack_raw = await ws.recv()
            ack = json.loads(ack_raw)
            if "sessionId" not in ack:
                raise RuntimeError(f"handshake failed: {ack}")
            self.ready.set()
            reducer = AsrReducer(self.speaker)
            receiver = asyncio.create_task(self._receive(ws, reducer))
            try:
                while True:
                    item = await self._queue.get()
                    if item is _DEAD:
                        break
                    if item is None:
                        await ws.send(json.dumps(end_stream_message()))
                        break
                    await ws.send(item)
                await receiver
            finally:
                if not receiver.done():
                    receiver.cancel()

    async def _receive(self, ws, reducer: AsrReducer) -> None:
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                if event.get("type") == "transcript" and not event.get("final") and self.on_partial:
                    self.on_partial(self.speaker, event.get("transcript") or "")
                turn = reducer.apply(event)
                if turn and turn.text.strip():
                    self.on_final(turn)
        finally:
            self._queue.put_nowait(_DEAD)


def wav_pcm_24k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        pcm = handle.readframes(handle.getnframes())
        converted, _state = pcm_to_mono_24k(
            pcm,
            handle.getsampwidth(),
            handle.getnchannels(),
            handle.getframerate(),
            None,
        )
    return converted
