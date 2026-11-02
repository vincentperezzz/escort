"""Post-meeting summary: Muse Spark when a key is set, a rules recap built from the store otherwise.

Both paths return the same shape:
    {status, title, bullets: [str], commitments: [{who, what, due}],
     conflicts: [{title, section}], generated_at, source, error}
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from escort.settings import Settings
from escort.spark.muse import spark_model_name
from escort.store import utc_now

MAX_TRANSCRIPT_CHARS = 20000


def empty_summary(status: str = "idle") -> dict[str, Any]:
    return {
        "status": status,
        "title": "",
        "bullets": [],
        "commitments": [],
        "conflicts": [],
        "generated_at": None,
        "source": None,
        "error": None,
    }


def _commitments(meeting: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"who": str(item.get("who") or "Someone"), "what": str(item.get("what") or ""), "due": str(item.get("when") or "unspecified")}
        for item in meeting.get("commitments") or []
        if item.get("what")
    ]


def _conflicts(meeting: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"title": str(item.get("title") or ""), "section": str(item.get("prd_section") or "")}
        for item in meeting.get("open_items") or []
    ]


def rules_summary(snapshot: dict[str, Any], *, error: str | None = None) -> dict[str, Any]:
    """Always useful, never raises: counts, conflicts, promises and the last things each side said."""
    meeting = snapshot.get("meeting") or {}
    turns = list(meeting.get("turns") or snapshot.get("utterances") or [])
    cards = list(snapshot.get("cards") or [])
    commitments = _commitments(meeting)
    conflicts = _conflicts(meeting)
    bullets: list[str] = []
    if turns:
        me = sum(1 for turn in turns if turn.get("speaker") == "Me")
        bullets.append(f"{len(turns)} transcribed turns ({me} from you, {len(turns) - me} from the other side).")
    else:
        bullets.append("No speech was transcribed in this meeting.")
    for item in conflicts[:4]:
        bullets.append(f"Open against the notes: {item['title']}" + (f" ({item['section']})" if item["section"] else "") + ".")
    for item in commitments[:4]:
        bullets.append(f"{item['who']} will {item['what']} ({item['due']}).")
    replies = [card for card in cards if card.get("type") == "SUGGESTED_REPLY"]
    if replies:
        bullets.append(f"Escort suggested {len(replies)} repl{'y' if len(replies) == 1 else 'ies'} during the call.")
    them = [turn for turn in turns if turn.get("speaker") != "Me" and len(str(turn.get("text") or "")) > 12]
    for turn in them[-2:]:
        bullets.append(f"They said: “{_clip(str(turn.get('text')))}”")
    if not conflicts and not commitments and turns:
        bullets.append("No scope conflicts or promises were detected.")
    title = str(snapshot.get("title") or "").strip() or "Meeting summary"
    return {
        **empty_summary("ready"),
        "title": title,
        "bullets": bullets,
        "commitments": commitments,
        "conflicts": conflicts,
        "generated_at": utc_now(),
        "source": "rules",
        "error": error,
    }


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def summary_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "MeetingSummary",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "commitments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"who": {"type": "string"}, "what": {"type": "string"}, "due": {"type": "string"}},
                            "required": ["who", "what", "due"],
                        },
                    },
                    "conflicts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"title": {"type": "string"}, "section": {"type": "string"}},
                            "required": ["title", "section"],
                        },
                    },
                },
                "required": ["title", "bullets", "commitments", "conflicts"],
            },
        },
    }


def build_summary_request(settings: Settings, snapshot: dict[str, Any], notes: str) -> dict[str, Any]:
    meeting = snapshot.get("meeting") or {}
    turns = meeting.get("turns") or []
    def _fmt_turn(turn: dict[str, Any]) -> str:
        vs = turn.get("vendor_speaker")
        tag = f" [Speaker {vs}]" if vs else ""
        return f"{turn.get('speaker')}{tag}: {turn.get('text')}"

    transcript = "\n".join(_fmt_turn(turn) for turn in turns)[-MAX_TRANSCRIPT_CHARS:]
    cards = [
        {key: card.get(key) for key in ("type", "title", "body", "prd_section", "who", "what", "when")}
        for card in snapshot.get("cards") or []
    ]
    text = (
        f"NOTES / SCOPE:\n{notes[:16000] or '(none loaded)'}\n\n"
        f"TRANSCRIPT (Me is the user, Them is the client):\n{transcript or '(empty)'}\n\n"
        f"CARDS ESCORT RAISED LIVE:\n{json.dumps(cards, ensure_ascii=False)[:8000]}"
    )
    return {
        "model": spark_model_name(settings.spark_model),
        "reasoning_effort": "minimal",
        "max_completion_tokens": 1600,
        "response_format": summary_schema(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarise a finished client call for the user (Me) in plain, natural English. "
                    "title: a short meeting title. bullets: 3-6 concrete takeaways in plain English, "
                    "attributing who said or requested what (using real participant names from the notes or transcript "
                    "when available and correcting obvious ASR name misspellings). "
                    "commitments: every promise, who is Me or Them (or the identified speaker name), due is the deadline or 'unspecified'. "
                    "conflicts: requests or statements that do not align with the loaded notes OR that contradict "
                    "what was stated/agreed in the first part of the call (section is the notes heading, or 'Earlier in this call' when no prior notes were loaded). "
                    "Use only what is in the transcript, notes, and cards. Never invent."
                ),
            },
            {"role": "user", "content": text},
        ],
    }


def parse_summary_response(payload: dict[str, Any]) -> dict[str, Any]:
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Muse returned an empty summary")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    data = json.loads(cleaned)
    bullets = [str(item).strip() for item in data.get("bullets") or [] if str(item).strip()]
    if not bullets:
        raise ValueError("Muse summary had no bullets")
    return {
        "title": str(data.get("title") or "").strip(),
        "bullets": bullets,
        "commitments": [
            {"who": str(item.get("who") or ""), "what": str(item.get("what") or ""), "due": str(item.get("due") or "unspecified")}
            for item in data.get("commitments") or []
            if isinstance(item, dict) and item.get("what")
        ],
        "conflicts": [
            {"title": str(item.get("title") or ""), "section": str(item.get("section") or "")}
            for item in data.get("conflicts") or []
            if isinstance(item, dict) and item.get("title")
        ],
    }


def generate_summary(
    settings: Settings,
    snapshot: dict[str, Any],
    notes: str = "",
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Muse summary when possible; otherwise (or on any failure) the rules summary. Status is always 'ready'."""
    rules = rules_summary(snapshot)
    if not settings.api_key:
        return rules
    owns = client is None
    http = client or httpx.Client(timeout=60)
    try:
        response = http.post(
            f"{settings.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
            json=build_summary_request(settings, snapshot, notes),
        )
        response.raise_for_status()
        parsed = parse_summary_response(response.json())
    except Exception as exc:
        return {**rules, "error": f"Muse summary failed, showing the local recap: {exc}"}
    finally:
        if owns:
            http.close()
    return {
        **rules,
        "title": parsed["title"] or rules["title"],
        "bullets": parsed["bullets"],
        # Keep what Escort recorded live when the model returns nothing for a list.
        "commitments": parsed["commitments"] or rules["commitments"],
        "conflicts": parsed["conflicts"] or rules["conflicts"],
        "source": spark_model_name(settings.spark_model),
    }
