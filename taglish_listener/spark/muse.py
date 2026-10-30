from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx
from PIL import Image

from taglish_listener.models import CARD_TYPES, CardDraft, FrameRecord, Utterance
from taglish_listener.settings import Settings


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


def build_spark_request(
    *,
    model: str,
    prd_text: str,
    utterances: list[Utterance],
    trigger: Utterance,
    frame: FrameRecord | None,
) -> dict[str, Any]:
    lines = [f"{item.speaker}: {item.text}" for item in utterances[-8:]]
    transcript = "\n".join(lines) if lines else "(empty)"
    text = (
        "PRD:\n"
        f"{prd_text[:24000]}\n\n"
        "Transcript window:\n"
        f"{transcript}\n\n"
        "The utterance that just finished, and the only one you should judge:\n"
        f"{trigger.speaker}: {trigger.text}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if frame is not None:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(frame.path), "detail": "low"},
            }
        )
    return {
        "model": model,
        "reasoning_effort": "low",
        "max_completion_tokens": 1200,
        "response_format": spark_schema(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the scope guard for a live Taglish client call. "
                    "Me is the user. Them is the client. Speaker labels are already correct "
                    "because the mic and the system audio were captured as separate streams. "
                    "Return only PRD_CONFLICT, COMMITMENT, and SUGGESTED_REPLY cards. "
                    "PRD_CONFLICT must cite a real heading from the PRD in prd_section. "
                    "COMMITMENT sets who to Me or Them and what to the promise. "
                    "SUGGESTED_REPLY is one natural Taglish sentence Me can say out loud, "
                    "code-switched the way the call already sounds, not a translation. "
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
        if card_type == "PRD_CONFLICT" and not (item.get("prd_section") or "").strip():
            continue
        cards.append(
            CardDraft(
                type=card_type,
                title=title,
                body=body,
                prd_section=(item.get("prd_section") or None),
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
        response = self._client.post(
            f"{self.settings.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json=request,
        )
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
