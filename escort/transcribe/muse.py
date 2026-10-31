from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path

import httpx

from escort.models import Turn
from escort.settings import Settings
from escort.transcribe.base import TranscriberUnavailable
from escort.transcribe.events import (
    AsrReducer,
    asr_urls,
    file_request,
    handshake_frame,
    turns_from_file_payload,
)


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
                    "Accept": "application/json",
                },
                files={
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": (path.name, audio, "audio/wav"),
                },
            )
            response.raise_for_status()
            return turns_from_file_payload(response.json(), speaker)
        finally:
            if self._owns_client and self._client is None:
                client.close()


class MuseRealtimeSession:
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

    def write(self, pcm: bytes) -> None:
        if self._loop is None or self._queue is None:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, pcm)

    def close(self) -> None:
        if self._loop is None or self._queue is None:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
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
                    if item is None:
                        await ws.send(json.dumps({"type": "endStream"}))
                        break
                    await ws.send(item)
                await receiver
            finally:
                if not receiver.done():
                    receiver.cancel()

    async def _receive(self, ws, reducer: AsrReducer) -> None:
        async for message in ws:
            if isinstance(message, bytes):
                continue
            event = json.loads(message)
            if event.get("type") == "transcript" and not event.get("final") and self.on_partial:
                self.on_partial(self.speaker, event.get("transcript") or "")
            turn = reducer.apply(event)
            if turn and turn.text.strip():
                self.on_final(turn)
