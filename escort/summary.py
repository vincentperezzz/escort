"""Post-meeting summary: Muse Spark when a key is set, a rules recap built from the store otherwise.

Both paths return the same shape:
    {status, title, bullets: [str], takeaways: [str], decisions: [str], open_questions: [str],
     commitments: [{who, what, due}], conflicts: [{title, section}], generated_at, source, error}

`bullets` are the meeting's highlights; there is no fixed count. Long transcripts are summarised
map-reduce style: one "segment notes" call per chunk, then the final summary over those notes.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from escort.settings import Settings
from escort.spark.muse import spark_model_name
from escort.store import utc_now

MAX_TRANSCRIPT_CHARS = 20000  # above this the transcript is chunked instead of sent raw
CHUNK_CHARS = 17000  # target size of one segment-notes chunk
TAIL_CHARS = 4000  # raw tail kept next to the segment notes, for recency
MIN_COMPLETION_TOKENS = 1600
MAX_COMPLETION_TOKENS = 6000


def empty_summary(status: str = "idle") -> dict[str, Any]:
    return {
        "status": status,
        "title": "",
        "bullets": [],
        "takeaways": [],
        "decisions": [],
        "open_questions": [],
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


def _undated_questions(commitments: list[dict[str, str]]) -> list[str]:
    out = []
    for item in commitments:
        if str(item.get("due") or "").strip().lower() in {"", "unspecified", "none", "tbd"}:
            who = "you" if item["who"] == "Me" else item["who"]
            out.append(f"When will {who} {item['what'].rstrip('.')}? No date was set.")
    return out


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
        "open_questions": _undated_questions(commitments),
        "commitments": commitments,
        "conflicts": conflicts,
        "generated_at": utc_now(),
        "source": "rules",
        "error": error,
    }


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---- request building ----------------------------------------------------------------------

_STRINGS: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_COMMITMENTS: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"who": {"type": "string"}, "what": {"type": "string"}, "due": {"type": "string"}},
        "required": ["who", "what", "due"],
    },
}


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
                    "bullets": _STRINGS,
                    "takeaways": _STRINGS,
                    "decisions": _STRINGS,
                    "open_questions": _STRINGS,
                    "commitments": _COMMITMENTS,
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
                "required": ["title", "bullets", "takeaways", "decisions", "open_questions", "commitments", "conflicts"],
            },
        },
    }


def segment_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "MeetingSegmentNotes",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "highlights": _STRINGS,
                    "decisions": _STRINGS,
                    "commitments": _COMMITMENTS,
                    "open_questions": _STRINGS,
                },
                "required": ["highlights", "decisions", "commitments", "open_questions"],
            },
        },
    }


def _fmt_turn(turn: dict[str, Any]) -> str:
    vs = turn.get("vendor_speaker")
    tag = f" [Speaker {vs}]" if vs else ""
    return f"{turn.get('speaker')}{tag}: {turn.get('text')}"


def transcript_lines(snapshot: dict[str, Any]) -> list[str]:
    meeting = snapshot.get("meeting") or {}
    return [_fmt_turn(turn) for turn in meeting.get("turns") or []]


def chunk_transcript(lines: list[str], size: int = CHUNK_CHARS) -> list[str]:
    """Split on turn boundaries into chunks of about `size` chars; a single huge turn is hard-split."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        for piece in [line[i : i + size] for i in range(0, len(line), size)] or [""]:
            if current and length + len(piece) + 1 > size:
                chunks.append("\n".join(current))
                current, length = [], 0
            current.append(piece)
            length += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def completion_budget(transcript_chars: int) -> int:
    """More room for longer meetings: ~1 output token per 12 transcript chars, floored and capped (~70k chars hits the cap)."""
    return max(MIN_COMPLETION_TOKENS, min(MAX_COMPLETION_TOKENS, transcript_chars // 12))


def _visuals(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    items = snapshot.get("snapshots")
    if items is None:
        items = (snapshot.get("meeting") or {}).get("snapshots")
    return [item for item in items or [] if isinstance(item, dict)]


def _visuals_block(snapshot: dict[str, Any]) -> str:
    lines = []
    for item in _visuals(snapshot):
        caption = " ".join(str(item.get("caption") or "").split())
        if not caption:
            continue
        kind = str(item.get("kind") or "visual")
        at = f" at {item['captured_at']}" if item.get("captured_at") else ""
        lines.append(f"- [{kind}{at}] {caption}")
    return "\n".join(lines)


_SYSTEM = (
    "Summarise a finished client call for the user (Me) in plain, natural English. "
    "title: a short meeting title. "
    "bullets: the meeting's highlights, as many as the content genuinely warrants. Let the amount of important "
    "material decide the count, not the meeting's length: a quick call with little to say may need only a few; "
    "a long, dense meeting must keep every real highlight from beginning to end. Never pad a short call, never "
    "truncate a dense one, and never group separate points together just to hit a count. Each bullet is one concrete "
    "point, attributing who said or requested what (using real participant names from the notes or transcript when "
    "available and correcting obvious ASR name misspellings). A highlight may reference a visual that was shown. "
    "takeaways: the key takeaways and implications for Me (what this means for the work, risks, next moves); may be empty. "
    "decisions: decisions actually made in the call, not proposals; may be empty. "
    "open_questions: unresolved questions and vague points that need follow-up; may be empty. "
    "commitments: every promise, who is Me or Them (or the identified speaker name), due is the deadline or 'unspecified'. "
    "conflicts: requests or statements that do not align with the loaded notes OR that contradict "
    "what was stated/agreed in the first part of the call (section is the notes heading, or 'Earlier in this call' "
    "when no prior notes were loaded). "
    "Use only what is in the transcript, segment notes, notes, visuals and cards. Never invent."
)


def build_summary_request(
    settings: Settings,
    snapshot: dict[str, Any],
    notes: str,
    segment_notes: list[str] | None = None,
) -> dict[str, Any]:
    transcript = "\n".join(transcript_lines(snapshot))
    if segment_notes:
        body = (
            "SEGMENT NOTES (the whole meeting in order, one block per part; cover all of it):\n"
            + "\n\n".join(segment_notes)
            + f"\n\nLAST PART OF THE TRANSCRIPT, VERBATIM (Me is the user, Them is the client):\n{transcript[-TAIL_CHARS:]}"
        )
    else:
        body = f"TRANSCRIPT (Me is the user, Them is the client):\n{transcript[-MAX_TRANSCRIPT_CHARS:] or '(empty)'}"
    cards = [
        {key: card.get(key) for key in ("type", "title", "body", "prd_section", "who", "what", "when")}
        for card in snapshot.get("cards") or []
    ]
    visuals = _visuals_block(snapshot)
    text = (
        f"NOTES / SCOPE:\n{notes[:16000] or '(none loaded)'}\n\n"
        f"{body}\n\n"
        + (f"VISUALS SHOWN (screenshots captured during the call):\n{visuals}\n\n" if visuals else "")
        + f"CARDS ESCORT RAISED LIVE:\n{json.dumps(cards, ensure_ascii=False)[:8000]}"
    )
    return {
        "model": spark_model_name(settings.spark_model),
        "reasoning_effort": "minimal",
        "max_completion_tokens": completion_budget(len(transcript)),
        "response_format": summary_schema(),
        "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": text}],
    }


def build_segment_request(settings: Settings, chunk: str, index: int, total: int) -> dict[str, Any]:
    return {
        "model": spark_model_name(settings.spark_model),
        "reasoning_effort": "minimal",
        "max_completion_tokens": MIN_COMPLETION_TOKENS,
        "response_format": segment_schema(),
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Take notes on part {index} of {total} of a client call transcript (Me is the user, Them is the client). "
                    "highlights: every important point in this part, as many as it warrants, attributing who said what. "
                    "decisions: decisions actually made. commitments: promises (who, what, due or 'unspecified'). "
                    "open_questions: unresolved or vague points. Lists may be empty. Use only the transcript. Never invent."
                ),
            },
            {"role": "user", "content": f"TRANSCRIPT, PART {index} OF {total}:\n{chunk}"},
        ],
    }


# ---- response parsing ----------------------------------------------------------------------


def _content_json(payload: dict[str, Any]) -> dict[str, Any]:
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
    if not isinstance(data, dict):
        raise ValueError("Muse summary was not a JSON object")
    return data


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_commitments(value: Any) -> list[dict[str, str]]:
    return [
        {"who": str(item.get("who") or ""), "what": str(item.get("what") or ""), "due": str(item.get("due") or "unspecified")}
        for item in value or []
        if isinstance(item, dict) and item.get("what")
    ]


def parse_summary_response(payload: dict[str, Any]) -> dict[str, Any]:
    data = _content_json(payload)
    bullets = _strings(data.get("bullets"))
    if not bullets:
        raise ValueError("Muse summary had no bullets")
    return {
        "title": str(data.get("title") or "").strip(),
        "bullets": bullets,
        "takeaways": _strings(data.get("takeaways")),
        "decisions": _strings(data.get("decisions")),
        "open_questions": _strings(data.get("open_questions")),
        "commitments": _parse_commitments(data.get("commitments")),
        "conflicts": [
            {"title": str(item.get("title") or ""), "section": str(item.get("section") or "")}
            for item in data.get("conflicts") or []
            if isinstance(item, dict) and item.get("title")
        ],
    }


def parse_segment_response(payload: dict[str, Any]) -> dict[str, Any]:
    data = _content_json(payload)
    return {
        "highlights": _strings(data.get("highlights")),
        "decisions": _strings(data.get("decisions")),
        "commitments": _parse_commitments(data.get("commitments")),
        "open_questions": _strings(data.get("open_questions")),
    }


def format_segment_notes(notes: dict[str, Any], index: int, total: int) -> str:
    lines = [f"Part {index} of {total}:"]
    sections = (
        ("Highlights", notes["highlights"]),
        ("Decisions", notes["decisions"]),
        ("Commitments", [f"{c['who']}: {c['what']} ({c['due']})" for c in notes["commitments"]]),
        ("Open questions", notes["open_questions"]),
    )
    for heading, items in sections:
        if items:
            lines.append(f"{heading}:")
            lines.extend(f"- {item}" for item in items)
    if len(lines) == 1:
        lines.append("- Nothing notable in this part.")
    return "\n".join(lines)


# ---- orchestration -------------------------------------------------------------------------


def _chat(http: httpx.Client, settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    response = http.post(
        f"{settings.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
        json=body,
    )
    response.raise_for_status()
    return response.json()


def _segment_notes(http: httpx.Client, settings: Settings, chunks: list[str]) -> list[str]:
    total = len(chunks)
    out = []
    for index, chunk in enumerate(chunks, start=1):
        payload = _chat(http, settings, build_segment_request(settings, chunk, index, total))
        out.append(format_segment_notes(parse_segment_response(payload), index, total))
    return out


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
        lines = transcript_lines(snapshot)
        segments = None
        if len("\n".join(lines)) > MAX_TRANSCRIPT_CHARS:
            segments = _segment_notes(http, settings, chunk_transcript(lines))
        parsed = parse_summary_response(_chat(http, settings, build_summary_request(settings, snapshot, notes, segments)))
    except Exception as exc:
        return {**rules, "error": f"Muse summary failed, showing the local recap: {exc}"}
    finally:
        if owns:
            http.close()
    return {
        **rules,
        "title": parsed["title"] or rules["title"],
        "bullets": parsed["bullets"],
        "takeaways": parsed["takeaways"],
        "decisions": parsed["decisions"],
        "open_questions": parsed["open_questions"] or rules["open_questions"],
        # Keep what Escort recorded live when the model returns nothing for a list.
        "commitments": parsed["commitments"] or rules["commitments"],
        "conflicts": parsed["conflicts"] or rules["conflicts"],
        "source": spark_model_name(settings.spark_model),
    }
