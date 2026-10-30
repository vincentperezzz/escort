from __future__ import annotations

from dataclasses import dataclass

from taglish_listener.models import Turn


class AsrError(RuntimeError):
    pass


@dataclass
class AsrReducer:
    stream_speaker: str
    open_turn: int | None = None
    partial: str = ""
    vendor_speaker: str | None = None

    def apply(self, event: dict) -> Turn | None:
        kind = event.get("type")
        if kind == "speechStart":
            self.open_turn = event.get("turnId")
            self.partial = ""
            return None
        if kind == "transcript":
            text = event.get("transcript") or ""
            if event.get("final"):
                return Turn(
                    speaker=self.stream_speaker,
                    text=text,
                    end_ms=int(event.get("audioProcessedMs") or 0),
                    vendor_speaker=self.vendor_speaker,
                )
            self.partial = text
            return None
        if kind == "speaker":
            self.vendor_speaker = event.get("label")
            return None
        if kind == "speechComplete":
            text = event.get("transcript") or self.partial
            turn = Turn(
                speaker=self.stream_speaker,
                text=text,
                end_ms=int(event.get("audioProcessedMs") or 0),
                turn_id=event.get("turnId"),
                vendor_speaker=self.vendor_speaker,
            )
            self.partial = ""
            self.open_turn = None
            self.vendor_speaker = None
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


def turns_from_file_payload(payload: dict, speaker: str) -> list[Turn]:
    raw_turns = payload.get("turns") or []
    turns: list[Turn] = []
    for item in raw_turns:
        text = (item.get("transcript") or "").strip()
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
    text = (payload.get("transcript") or "").strip()
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
