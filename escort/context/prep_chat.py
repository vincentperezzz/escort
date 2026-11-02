from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from escort.settings import Settings, load_settings
from escort.spark.muse import _data_url, spark_model_name


def prep_chat(
    message: str,
    context_markdown: str,
    history: list[dict[str, str]],
    http: httpx.Client | None = None,
    settings_note: str = "",
    *,
    settings: Settings | None = None,
    meeting_note: str = "",
    frame_path: str | None = None,
) -> str:
    cfg = settings or load_settings()
    if not cfg.api_key:
        return (
            "Set MODEL_API_KEY in Settings first so Escort AI can answer. "
            "Once added, you can ask about your meeting, notes, live transcript, or how to respond."
        )
    model_id = spark_model_name(cfg.spark_model)
    chat_turns = "\n".join(
        f"{turn.get('role', 'user')}: {turn.get('content', '')}"
        for turn in history[-10:]
        if turn.get("content")
    )
    notes_block = (
        context_markdown[:24000]
        if context_markdown and context_markdown.strip()
        else "(No notes or documents are currently loaded in the Context library.)"
    )
    sections = [
        f"LOADED CONTEXT / NOTES:\n{notes_block}",
    ]
    if meeting_note and meeting_note.strip():
        sections.append(f"LIVE MEETING & CALENDAR STATE:\n{meeting_note.strip()[:16000]}")
    if chat_turns:
        sections.append(f"RECENT CHAT HISTORY:\n{chat_turns}")
    sections.append(f"USER MESSAGE:\n{message}")
    prompt = "\n\n".join(sections)

    system = (
        "You are Escort AI, the intelligent AI meeting copilot inside Escort (a desktop copilot for live and pre-meeting client calls). "
        "When greeting the user or introducing yourself, introduce yourself as Escort AI (e.g., 'Hi — I'm Escort AI, your meeting copilot.').\n"
        f"Runtime & model identity: You run on Meta Muse Spark ({model_id}), paired with Muse Voice Transcribe "
        f"({cfg.asr_model}) for real-time speech-to-text (Me = user's microphone, Them = remote call audio) "
        f"and SAM 3.1 ({cfg.sam_model}) for visual screen grounding.\n\n"
        "Follow these core principles on every response:\n"
        "1. LANGUAGE: Respond in clear, natural English by default, or match the language the user used in their message. "
        "Do NOT default to Taglish unless the user writes in Taglish/Filipino or explicitly asks for a Taglish response.\n"
        "2. FOCUS ON WHAT THE USER WANTS: Answer the user's question directly and helpfully. Never deflect with "
        "'that is not in the loaded notes so I cannot help', 'there are no notes loaded yet so I cannot prep anything', "
        "or 'I am a note-taking app and cannot help with that'. If no notes are loaded or the user asks a general, "
        "strategic, technical, or app-related question (e.g., greeting you, asking what model you use, asking how to respond, "
        "or brainstorming), help them directly! Only mention what is or isn't in the loaded notes when verifying scope or when "
        "the user specifically asks about their notes (and if no notes are loaded yet, you can briefly mention they can add "
        "Google Docs, Notion pages, or files in the Context tab).\n"
        "3. FUNCTION WITH OR WITHOUT PRIOR NOTES & FLAG INTRA-MEETING CONTRADICTIONS (TOP PRIORITY): "
        "If prior notes/PRD are loaded, continuously match patterns across the loaded notes, the live transcript, "
        "and the screen, and flag anything that does not align with the loaded notes (citing the note section). "
        "If the meeting started WITHOUT prior notes, seamlessly adjust to live note-taking & intra-meeting alignment mode: "
        "actively take structured notes from the conversation and screen, treat what was agreed or stated in the first "
        "part of the meeting as the baseline, and proactively flag whenever later statements or screen content contradict, "
        "drift from, or do not align with what was said in the first/earlier part of the meeting!\n"
        "4. PIECE TOGETHER SPEAKERS, SCREEN & FIX MISSPELLED NAMES: Determine who is talking, who mentioned what, and who "
        "is on the meeting screen. In the live transcript, 'Me' is the user and 'Them' is the remote participants' audio. "
        "Cross-reference the attached screen frame (Zoom/Meet/Teams participant tiles, active-speaker highlight, open/muted "
        "mic icons, shared presentation slides/docs, chat names), calendar attendees, dialogue cues, and loaded notes to "
        "attach real participant names to who said or promised what (e.g., 'Maria (Them)'), and use those names to fix "
        "misspelled or phonetically misheard names and terms from live speech-to-text.\n"
        "5. FOLLOW-UPS & HONEST CLARIFICATION: Support follow-ups like 'What did she say?', 'Who mentioned that?', or "
        "'What should I respond?' by synthesizing the latest transcript turns, in-progress speech, screen frame, and notes. "
        "If the transcript or audio is messy, cut off, or ambiguous about who spoke or what was meant, be honest about "
        "what is unclear and ask a brief, concrete clarifying question.\n"
        "Keep responses concise, natural, and directly actionable."
    )
    if settings_note:
        system = f"{system}\n\n{settings_note}"

    user_content: Any = prompt
    if frame_path:
        path_obj = Path(frame_path)
        if path_obj.is_file():
            try:
                user_content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _data_url(str(path_obj), max_edge=1024), "detail": "auto"}},
                ]
            except Exception:
                user_content = prompt

    client = http or httpx.Client(timeout=60)
    owns = http is None
    try:
        last_err: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = client.post(
                    f"{cfg.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cfg.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "reasoning_effort": "minimal",
                        "max_completion_tokens": 1600,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user_content},
                        ],
                    },
                )
                break
            except httpx.TransportError as err:
                last_err = err
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
        if response is None:
            raise last_err or RuntimeError("Network request failed")
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content).strip() or "No response from Escort AI."
    except httpx.TransportError as err:
        return f"Escort AI couldn't reach the model server ({err}). Check your internet connection and try again."
    except Exception as err:
        return f"Escort AI is quiet right now ({err}). Try again after checking the API key."
    finally:
        if owns:
            client.close()
