from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx
from PIL import Image

from escort.models import CARD_TYPES, CardDraft, FrameRecord, Utterance
from escort.settings import Settings


class SparkError(RuntimeError):
    pass


def _data_url(path: str, max_edge: int = 768) -> str:
    image = Image.open(path)
    image.thumbnail((max_edge, max_edge))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=70)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def spark_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ScopeCards",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cards": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {"type": "string", "enum": list(CARD_TYPES)},
                                "title": {"type": "string"},
                                "body": {"type": "string"},
                                "prd_section": {"type": ["string", "null"]},
                                "who": {"type": ["string", "null"]},
                                "what": {"type": ["string", "null"]},
                            },
                            "required": ["type", "title", "body", "prd_section", "who", "what"],
                        },
                    }
                },
                "required": ["cards"],
            },
        },
    }


def spark_model_name(model: str) -> str:
    cleaned = (model or "muse-spark-1.3").strip()
    if cleaned.endswith("-contributor"):
        return "muse-spark-1.3"
    return cleaned


def _format_utterance(item: Utterance) -> str:
    if item.vendor_speaker:
        return f"{item.speaker} [Speaker {item.vendor_speaker}]: {item.text}"
    return f"{item.speaker}: {item.text}"


def build_spark_request(
    *,
    model: str,
    prd_text: str,
    utterances: list[Utterance],
    trigger: Utterance,
    frame: FrameRecord | None,
) -> dict[str, Any]:
    if len(utterances) > 24:
        early = [_format_utterance(item) for item in utterances[:10]]
        recent = [_format_utterance(item) for item in utterances[-18:]]
        transcript = (
            "[First part of the meeting (baseline statements & agreements)]\n"
            + "\n".join(early)
            + f"\n... ({len(utterances) - 28} intermediate turns omitted) ...\n"
            + "[Recent part of the meeting]\n"
            + "\n".join(recent)
        )
    else:
        lines = [_format_utterance(item) for item in utterances]
        transcript = "\n".join(lines) if lines else "(empty)"
    has_notes = bool(prd_text and prd_text.strip())
    notes_block = (
        prd_text[:24000]
        if has_notes
        else "(No prior notes loaded — operate in live note-taking & intra-meeting alignment mode. Use statements and agreements from the first/earlier part of this call as the baseline.)"
    )
    text = (
        "PRD / Loaded Notes:\n"
        f"{notes_block}\n\n"
        "Transcript window:\n"
        f"{transcript}\n\n"
        "The utterance that just finished, and the primary turn you should judge:\n"
        f"{_format_utterance(trigger)}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if frame is not None:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(frame.path, max_edge=1024), "detail": "auto"},
            }
        )
    return {
        "model": spark_model_name(model),
        "reasoning_effort": "minimal",
        "max_completion_tokens": 1600,
        "response_format": spark_schema(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the live alignment, scope guard, and note-taking copilot for a client meeting. "
                    "Me is the user (microphone stream). Them is the remote side (system audio stream). "
                    "When a screen frame is attached, inspect the meeting window (participant tiles, active speaker "
                    "highlight, open/muted mic indicators, shared slides/documents, and chat names) together with "
                    "the PRD/notes and transcript to piece together who is speaking, who is on the screen, and who "
                    "mentioned or promised what, and use visible names on screen or in the PRD to fix misspelled or "
                    "phonetically misheard ASR names. "
                    "Return only PRD_CONFLICT, COMMITMENT, and SUGGESTED_REPLY cards. "
                    "1) WITH OR WITHOUT PRIOR NOTES: If no prior PRD/notes are loaded, seamlessly adjust and operate in "
                    "live note-taking & intra-meeting alignment mode — capture important commitments, action items, "
                    "key decisions, and agreed deliverables as COMMITMENT cards so the meeting is thoroughly noted. "
                    "2) INTRA-MEETING & NOTES ALIGNMENT (PRD_CONFLICT): Flag anything said or shown on screen that "
                    "does not align with EITHER the loaded PRD/notes OR what was stated/agreed in the first/earlier "
                    "part of this meeting! If it conflicts with loaded notes, cite the heading from the PRD in "
                    "prd_section. If it contradicts or drifts from what was said or agreed earlier in this meeting "
                    "(especially when no prior notes are loaded, or when a speaker contradicts an earlier agreement "
                    "from the start of the call), emit PRD_CONFLICT with prd_section set to 'Earlier in this call'. "
                    "3) COMMITMENT sets who to Me or Them (or the identified speaker name on Them) and what to the promise or key decision note. "
                    "4) SUGGESTED_REPLY is one natural sentence Me can say out loud, matching the language actually used "
                    "in the call (English by default if the conversation is in English; natural Taglish only if they are speaking Taglish). "
                    "Return an empty cards list when nothing new qualifies. "
                    "Do not invent a fourth card type."
                ),
            },
            {"role": "user", "content": content},
        ],
    }


def parse_spark_response(payload: dict[str, Any]) -> list[CardDraft]:
    choices = payload.get("choices") or []
    if not choices:
        raise SparkError("Muse Spark returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict):
                texts.append(part.get("text") or "")
            elif isinstance(part, str):
                texts.append(part)
        content = "\n".join(texts)
    if not isinstance(content, str) or not content.strip():
        raise SparkError("Muse Spark content was empty")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    data = json.loads(cleaned)
    cards: list[CardDraft] = []
    for item in data.get("cards") or []:
        card_type = item.get("type")
        if card_type not in CARD_TYPES:
            continue
        title = (item.get("title") or "").strip()
        body = (item.get("body") or "").strip()
        if not title or not body:
            continue
        prd_section = (item.get("prd_section") or "").strip() or None
        if card_type == "PRD_CONFLICT" and not prd_section:
            prd_section = "Earlier in this call"
        cards.append(
            CardDraft(
                type=card_type,
                title=title,
                body=body,
                prd_section=prd_section,
                who=(item.get("who") or None),
                what=(item.get("what") or None),
            )
        )
    return cards


class MuseSpark:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.api_key:
            raise SparkError("MODEL_API_KEY is not set")
        self.settings = settings
        self.name = settings.spark_model
        self._client = client or httpx.Client(timeout=60)

    def review(
        self,
        *,
        prd_text: str,
        utterances: list[Utterance],
        trigger: Utterance,
        frame: FrameRecord | None = None,
    ) -> list[CardDraft]:
        request = build_spark_request(
            model=self.settings.spark_model,
            prd_text=prd_text,
            utterances=utterances,
            trigger=trigger,
            frame=frame,
        )
        last_err: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = self._client.post(
                    f"{self.settings.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request,
                )
                break
            except httpx.TransportError as err:
                last_err = err
                if attempt < 2:
                    import time

                    time.sleep(0.35 * (attempt + 1))
        if response is None:
            raise last_err or SparkError("Network request failed")
        response.raise_for_status()
        return parse_spark_response(response.json())


class FallbackSpark:
    def __init__(self, primary: Any, fallback: Any, on_error) -> None:
        self.primary = primary
        self.fallback = fallback
        self.on_error = on_error
        self.name = getattr(primary, "name", "muse-spark")

    def review(self, **kwargs) -> list[CardDraft]:
        try:
            drafts = self.primary.review(**kwargs)
            self.name = self.primary.name
            return drafts
        except Exception as exc:
            self.on_error(f"Muse Spark failed, using the rules fallback: {exc}")
            self.name = self.fallback.name
            return self.fallback.review(**kwargs)
