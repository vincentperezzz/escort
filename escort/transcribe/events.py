from __future__ import annotations

import json
from dataclasses import dataclass

from escort.asr_text import dedupe_adjacent
from escort.models import Turn


class AsrError(RuntimeError):
    pass


@dataclass
class AsrReducer:
    stream_speaker: str
    open_turn: int | None = None
    partial: str = ""
    vendor_speaker: str | None = None
    start_ms: int = 0
    end_ms: int = 0

    def apply(self, event: dict) -> Turn | None:
        kind = event.get("type")
        if kind == "speechStart":
            self.open_turn = event.get("turnId")
            self.start_ms = int(event.get("audioProcessedMs") or event.get("startMs") or 0)
            self.end_ms = 0
            self.partial = ""
            return None
        if kind == "speechEnd":
            self.end_ms = int(event.get("audioProcessedMs") or event.get("endMs") or 0)
            return None
        if kind == "transcript":
            text = dedupe_adjacent(event.get("transcript") or "")
            if event.get("final"):
                return Turn(
                    speaker=self.stream_speaker,
                    text=text,
                    start_ms=self.start_ms,
                    end_ms=self.end_ms or int(event.get("audioProcessedMs") or 0),
                    vendor_speaker=self.vendor_speaker,
                )
            self.partial = text
            return None
        if kind == "speaker":
            self.vendor_speaker = event.get("label")
            return None
        if kind == "speechComplete":
            text = dedupe_adjacent(event.get("transcript") or self.partial)
            turn = Turn(
                speaker=self.stream_speaker,
                text=text,
                start_ms=self.start_ms,
                end_ms=self.end_ms or int(event.get("audioProcessedMs") or 0),
                turn_id=event.get("turnId"),
                vendor_speaker=self.vendor_speaker,
            )
            self.partial = ""
            self.open_turn = None
            self.vendor_speaker = None
            self.start_ms = 0
            self.end_ms = 0
            return turn
        if kind == "error":
            raise AsrError(event.get("message") or "asr error")
        return None


def handshake_frame(
    api_key: str,
    *,
    model: str,
    mode: str,
    language_bias: list[str],
    keywords: list[str],
) -> dict:
    return {
        "authorization": {"accessToken": f"Bearer {api_key}"},
        "audioEncoding": "PCM_24KHZ",
        "model": model,
        "mode": mode,
        "partialMode": "CUMULATIVE",
        "emitAudioProgress": False,
        "languageBias": language_bias,
        "keywords": keywords,
    }


def file_request(
    *,
    model: str,
    mode: str,
    language_bias: list[str],
    keywords: list[str],
) -> dict:
    return {
        "model": model,
        "audioEncoding": "WAV",
        "mode": mode,
        "languageBias": language_bias,
        "keywords": keywords,
    }


def end_stream_message() -> dict:
    return {"type": "endStream"}


def turns_from_sse(body: str, speaker: str) -> list[Turn]:
    reducer = AsrReducer(speaker)
    turns: list[Turn] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        event = json.loads(raw)
        turn = reducer.apply(event)
        if turn and turn.text.strip():
            turns.append(turn)
    return turns


def turns_from_file_payload(payload: dict, speaker: str) -> list[Turn]:
    raw_turns = payload.get("turns") or []
    turns: list[Turn] = []
    for item in raw_turns:
        text = dedupe_adjacent((item.get("transcript") or "").strip())
        if not text:
            continue
        turns.append(
            Turn(
                speaker=speaker,
                text=text,
                start_ms=int(item.get("startMs") or 0),
                end_ms=int(item.get("endMs") or 0),
                turn_id=item.get("turnId"),
                vendor_speaker=item.get("speaker"),
            )
        )
    if turns:
        return turns
    text = dedupe_adjacent((payload.get("transcript") or "").strip())
    if not text:
        return []
    return [
        Turn(
            speaker=speaker,
            text=text,
            start_ms=0,
            end_ms=int(payload.get("audioDurationMs") or 0),
        )
    ]


def asr_urls(base_url: str) -> tuple[str, str]:
    base = base_url.rstrip("/")
    http = f"{base}/asr/transcribe"
    ws = base.replace("https://", "wss://").replace("http://", "ws://") + "/asr/realtime"
    return http, ws
