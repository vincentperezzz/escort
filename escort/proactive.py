"""Proactive engine: Escort speaks up on its own during a meeting.

Two layers, one thread:

1. Signals (local, cheap, every TICK_S): elapsed time, whether the other side has spoken, the
   calendar slot and its join link, the foreground window, scene cuts from the frame gate, new
   cards. Some signals are decisive on their own and become nudges without a model call:
   NO_MEETING (listening but no call), TIME_CHECK (slot ending with loose ends), and, with no API
   key, NOTED / CLARIFY from commitment cards and WRONG_PROJECT from a window title that names
   another project.
2. Assessment (Muse Spark, rate-limited): when something meaningful changed (new talk, a scene
   cut, a new app in front), the model gets the project, the other projects, the slot, the notes,
   the recent transcript, what Escort already said, and the newest changed frame. It decides on
   its own what (if anything) is worth a tap on the shoulder, judges whether the screen belongs to
   this project, and whether the frame is a visual worth keeping in the notes.

Every nudge passes a filter: confidence floor, a per-session dedupe key, a cool-down per
priority, and a long snooze for kinds the user dismissed. Failures are logged as store warnings
and never stop the loop.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from PIL import Image

from escort.capture.foreground import foreground_window
from escort.diff import dhash, hamming
from escort.models import NUDGE_KINDS, NUDGE_PRIORITIES, NudgeRecord
from escort.settings import Settings
from escort.spark.muse import _data_url, spark_model_name
from escort.store import MeetingStore, new_id, utc_now

log = logging.getLogger("escort.proactive")

TICK_S = 2.0
NO_MEETING_AFTER_S = 60.0  # listening this long with no voice from the other side
NO_MEETING_IN_APP_AFTER_S = 180.0  # ...or this long when a meeting app is in front (waiting room)
TIME_CHECK_LEFT_S = 5 * 60
ASSESS_MIN_GAP_S = 12.0  # never call the model more often than this
ASSESS_TALK_GAP_S = 25.0  # new talk alone waits this long between calls
ASSESS_HEARTBEAT_S = 90.0
COOLDOWN_S = {"urgent": 20.0, "normal": 30.0, "low": 12.0}
DISMISS_SNOOZE_S = 10 * 60
MIN_CONFIDENCE = 0.6
WRONG_PROJECT_CONFIDENCE = 0.75
ACTION_IDS = ("open_link", "switch_project", "ask", "dismiss", "snooze")

MEETING_APPS = re.compile(r"teams|zoom|webex|google meet|meet\.google|meet -|slack huddle|discord", re.IGNORECASE)
VAGUE = re.compile(
    r"\b(soon|later|asap|sometime|next time|eventually|maybe|probably|i'?ll try|we'?ll see|tbd|"
    r"some(?:thing|how)|a few|a bit|kind of|sort of|etc)\b",
    re.IGNORECASE,
)

# What experienced meeting assistants and facilitators speak up about. A menu, not a script:
# the model is told to use its own judgment and may raise anything else under HEADS_UP.
TRIGGER_MENU = """\
- NO_MEETING: the user seems not to be in the call (silence from the other side, no meeting app, slot already started).
- WRONG_PROJECT: what is on screen or being discussed belongs to a different project than the active one
  (another repo, product, client or doc). Suggest switching project, or bringing the right material on screen.
- NOTED: a short acknowledgement that a substantive update, decision or scope change was captured,
  naming who said it (e.g. "Noted: Jessa's update on the RBAC scope"). Low priority. Only for things that matter.
- CLARIFY: something was agreed or requested vaguely: no owner, no date, no number, an undefined term
  ("the usual roles", "soon", "make it faster"), or two people mean different things. Give the exact question to ask.
- QUESTION_FOR_YOU: someone asked the user (Me) something that has not been answered, or the user was mentioned by name.
- TIME_CHECK: the slot is close to its end and agenda points, decisions or owners are still open.
- OFF_TOPIC: the talk drifted from the meeting's purpose for a while.
- HEADS_UP: anything else a sharp chief of staff would whisper: a risk, a contradiction with the notes or
  with something said earlier, a number that does not add up, a promise the user should not make, an acronym the user may not know."""


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def proactive_schema() -> dict[str, Any]:
    action = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "enum": list(ACTION_IDS)},
            "label": {"type": "string"},
            "value": {"type": ["string", "null"]},
        },
        "required": ["id", "label", "value"],
    }
    nudge = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": list(NUDGE_KINDS)},
            "priority": {"type": "string", "enum": list(NUDGE_PRIORITIES)},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "actions": {"type": "array", "items": action},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
            "key": {"type": "string"},
        },
        "required": ["kind", "priority", "title", "body", "actions", "reason", "confidence", "key"],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ProactiveAssessment",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "screen": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "subject": {"type": "string"},
                            "project_relevance": {"type": "string", "enum": ["matches", "other_project", "unrelated", "unclear"]},
                            "matching_project_id": {"type": ["string", "null"]},
                            "confidence": {"type": "number"},
                        },
                        "required": ["subject", "project_relevance", "matching_project_id", "confidence"],
                    },
                    "visual": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "keep": {"type": "boolean"},
                            "kind": {"type": "string", "enum": ["chart", "diagram", "wireframe", "sketch", "table", "slide", "other"]},
                            "caption": {"type": "string"},
                        },
                        "required": ["keep", "kind", "caption"],
                    },
                    "nudges": {"type": "array", "items": nudge},
                },
                "required": ["screen", "visual", "nudges"],
            },
        },
    }


SYSTEM_PROMPT = (
    "You are Escort, the user's (Me's) live meeting copilot. You always have the user's back: you watch the call "
    "the way a sharp chief of staff sitting next to them would, and you speak up only when it genuinely helps them "
    "right now. Me is the user (microphone). Them is the other side (system audio).\n\n"
    "Each turn you get the active project, the user's other projects, the calendar slot, the loaded notes, the recent "
    "transcript, the cards and nudges Escort already showed (with whether the user dismissed them), the window in "
    "front, and possibly the newest changed screen frame. Decide for yourself what, if anything, is worth saying. "
    "These are the situations meeting assistants typically raise; use them as a guide, not a checklist:\n"
    f"{TRIGGER_MENU}\n\n"
    "Rules:\n"
    "- Silence is the default. Return an empty nudges list unless a nudge would change what the user does in the next "
    "minute or saves them from a mistake. Never repeat or rephrase a nudge already shown; never re-raise one the user dismissed.\n"
    "- title: at most 9 words. body: one or two short sentences, plain and direct, speaking to the user. For CLARIFY "
    "and QUESTION_FOR_YOU, put the exact line the user can say in the body. Match the call's language (English by "
    "default, natural Taglish only if they speak Taglish). Use real names from the transcript, screen or notes.\n"
    "- priority: urgent only when the user is about to miss the meeting, agree to something wrong, or is being asked "
    "directly; normal for things to act on soon; low for NOTED acknowledgements.\n"
    "- confidence 0-1: how sure you are this is real and useful. Below 0.6 it is dropped.\n"
    "- key: a short stable slug for the underlying issue (e.g. 'rbac-scope-owner'), so the same issue is never raised twice.\n"
    "- actions (0-3): open_link (join the meeting), switch_project (value = the project id to switch to), ask "
    "(value = a question for Escort AI to answer for the user), snooze, dismiss. Labels at most 3 words.\n"
    "- screen: describe what is on screen in a few words (app, repo/doc/product name). project_relevance compares it "
    "with the ACTIVE project: 'matches' when it is this project's material (or the meeting app / neutral content), "
    "'other_project' when it clearly is one of the user's other projects (set matching_project_id), 'unrelated' when "
    "it clearly belongs to some other codebase, product or client, 'unclear' otherwise. Judge from repo names, window "
    "titles, file trees, product names, domain (e.g. Google APIs code vs a sales CRM), even with few details. When it is "
    "other_project or unrelated with confidence >= 0.75 and no WRONG_PROJECT nudge was shown recently, raise one: say what "
    "you see, offer switch_project when another project fits, and suggest bringing the right repo or doc on screen "
    "(name it if you can see it, e.g. a repo list in the sidebar).\n"
    "- visual.keep: true only when the frame shows something hard to carry in text that belongs in the notes: a chart, "
    "graph, architecture or flow diagram, wireframe, sketch, whiteboard, or a dense table. Not for code editors, chat, "
    "video tiles, plain text slides, or a visual already kept. caption: what it shows and why it matters, one sentence."
)


def build_proactive_request(model: str, payload: dict[str, Any], frame_path: str | None) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}]
    if frame_path:
        content.append({"type": "image_url", "image_url": {"url": _data_url(frame_path, max_edge=1280), "detail": "auto"}})
    return {
        "model": spark_model_name(model),
        "reasoning_effort": "low",
        "max_completion_tokens": 1400,
        "response_format": proactive_schema(),
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
    }


def parse_proactive_response(payload: dict[str, Any]) -> dict[str, Any]:
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty proactive assessment")
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    data = json.loads(cleaned)
    return {
        "screen": data.get("screen") if isinstance(data.get("screen"), dict) else {},
        "visual": data.get("visual") if isinstance(data.get("visual"), dict) else {},
        "nudges": [item for item in data.get("nudges") or [] if isinstance(item, dict)],
    }


class ProactiveEngine:
    """One per listening session. `context_fn()` returns:

    {"project": {id, name, description} | None, "other_projects": [{id, name, description}],
     "event": {title, start, end, meeting_url, attendees} | None, "notes": str}
    """

    def __init__(
        self,
        store: MeetingStore,
        settings: Settings,
        context_fn: Callable[[], dict[str, Any]],
        *,
        gate_fn: Callable[[], Any] | None = None,
        foreground_fn: Callable[[], dict[str, str] | None] = foreground_window,
        on_nudge: Callable[[NudgeRecord], None] | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.settings = settings
        self.context_fn = context_fn
        self.gate_fn = gate_fn
        self.foreground_fn = foreground_fn
        self.on_nudge = on_nudge
        self._client = client
        self._owns_client = False
        self.clock = clock
        self.now_fn = now_fn
        self.started = clock()
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._keys: set[str] = set()
        self._last_by_priority: dict[str, float] = {}
        self._snoozed: dict[str, float] = {}  # kind -> until
        self._seen_cards: set[str] = set()
        self._last_assess = -1e9
        self._assessed_utts = 0
        self._assessed_frame: str | None = None
        self._assessed_window = ""
        self._last_window = ""
        self._screen_subject = ""
        self._mismatch_streak = 0
        self._snapshot_hashes: list[int] = []
        self._context: dict[str, Any] = {}

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> ProactiveEngine:
        self._thread = threading.Thread(target=self._loop, name="escort-proactive", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 0.2) -> None:
        """Signal and return: an assessment in flight finishes on its own thread, which then exits."""
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        try:
            while not self.stop_event.wait(TICK_S):
                try:
                    self.tick()
                except Exception as exc:
                    log.warning("Proactive tick failed: %s", exc)
        finally:
            if self._client is not None and self._owns_client:
                self._client.close()

    # --- user feedback ---------------------------------------------------------

    def note_dismissed(self, nudge: NudgeRecord) -> None:
        self._snoozed[nudge.kind] = self.clock() + DISMISS_SNOOZE_S

    # --- one tick ----------------------------------------------------------------

    def tick(self) -> None:
        self._context = self._safe_context()
        window = self._window_label()
        if window:
            self._last_window = window
        self._rule_no_meeting(window)
        self._rule_time_check()
        if self.settings.api_key:
            self._maybe_assess(window)
        else:
            self._rules_from_cards()
            self._rule_window_project(window)

    def _safe_context(self) -> dict[str, Any]:
        try:
            return self.context_fn() or {}
        except Exception as exc:
            log.debug("Proactive context failed: %s", exc)
            return {}

    def _window_label(self) -> str:
        try:
            info = self.foreground_fn() or {}
        except Exception:
            return ""
        title, process = str(info.get("title") or ""), str(info.get("process") or "")
        if not title and not process:
            return ""
        return f"{title} ({process})" if process else title

    def _them_spoke(self) -> bool:
        with self.store.lock:
            if any(item.speaker != "Me" for item in self.store.utterances):
                return True
            return any(speaker != "Me" and text for speaker, text in self.store.partials.items())

    # --- deterministic rules -------------------------------------------------------

    def _rule_no_meeting(self, window: str) -> None:
        if "no_meeting" in self._keys or self._them_spoke():
            return
        elapsed = self.clock() - self.started
        in_app = bool(MEETING_APPS.search(window or ""))
        if elapsed < (NO_MEETING_IN_APP_AFTER_S if in_app else NO_MEETING_AFTER_S):
            return
        event = self._context.get("event") or {}
        title = str(event.get("title") or "").strip()
        url = str(event.get("meeting_url") or "").strip()
        start = _parse_time(event.get("start"))
        started_text = f" It started at {start.astimezone().strftime('%H:%M')}." if start and start <= self.now_fn() else ""
        if in_app:
            body = "A meeting app is open, but there's no audio from the other side yet. Still in the waiting room, or is the speaker device in Settings the right one?"
            actions = [{"id": "dismiss", "label": "All good"}]
            headline = "No voice from the other side yet"
        elif url:
            body = f"Nobody else has spoken yet. Did you forget to join{f' {title}' if title else ''}?{started_text}"
            actions = [{"id": "open_link", "label": "Join meeting", "value": url}, {"id": "dismiss", "label": "I'm in"}]
            headline = "Seems you're not in the meeting yet"
        elif title:
            body = f"No one has spoken and {title} has no join link on the invite. Maybe it was moved. Have you checked the latest team messages?"
            actions = [{"id": "dismiss", "label": "I'm in"}, {"id": "snooze", "label": "Later"}]
            headline = "Meeting may have been postponed"
        else:
            body = "Escort has been listening for a minute and no one else has spoken. Are you in the call yet?"
            actions = [{"id": "dismiss", "label": "I'm in"}]
            headline = "Not hearing a meeting yet"
        self._push("NO_MEETING", "urgent" if url else "normal", headline, body, actions, key="no_meeting", source="rules", reason="silence from the other side")

    def _rule_time_check(self) -> None:
        if "time_check" in self._keys:
            return
        end = _parse_time((self._context.get("event") or {}).get("end"))
        if end is None:
            return
        left = (end - self.now_fn()).total_seconds()
        if not 0 < left <= TIME_CHECK_LEFT_S or not self._them_spoke():
            return
        meeting = self.store.meeting()
        undated = [item for item in meeting["commitments"] if (item.get("when") or "unspecified") == "unspecified"]
        loose = len(meeting["open_items"]) + len(undated)
        if not loose:
            return
        minutes = max(1, round(left / 60))
        body = f"About {minutes} min left. {loose} item{'s' if loose != 1 else ''} still open or without a date. Worth confirming owners and dates before you drop."
        self._push(
            "TIME_CHECK",
            "normal",
            "Slot ending, loose ends open",
            body,
            [{"id": "ask", "label": "Wrap-up lines", "value": "Give me two lines to wrap up this call: confirm the open items, owners and dates."}],
            key="time_check",
            source="rules",
            reason=f"{minutes} min left",
        )

    def _rules_from_cards(self) -> None:
        """No model: acknowledge commitments, and ask for an owner/date when one is vague."""
        with self.store.lock:
            cards = [card for card in self.store.cards if card.type == "COMMITMENT" and card.id not in self._seen_cards]
        for card in cards:
            self._seen_cards.add(card.id)
            who, what = (card.who or "Someone"), _clip(card.what or card.title, 90)
            if (card.when or "unspecified") == "unspecified" or VAGUE.search(card.what or card.body or ""):
                self._push(
                    "CLARIFY",
                    "normal",
                    "Pin down this promise",
                    f"{who}: “{what}” has no clear date or scope. You could ask: “Just to confirm, who owns this and by when?”",
                    [],
                    key=f"clarify:{_norm(what)[:60]}",
                    source="rules",
                    reason="commitment without a date",
                )
            else:
                self._push("NOTED", "low", f"Noted: {who}", what, [], key=f"noted:{card.id}", source="rules", reason="commitment card")

    def _rule_window_project(self, window: str) -> None:
        """No model: a window title that names another project (and not this one) is a strong hint."""
        project = self._context.get("project") or {}
        if not window or not project:
            return
        title = _norm(window)
        own = _norm(str(project.get("name") or ""))
        if own and own in title:
            return
        for other in self._context.get("other_projects") or []:
            name = _norm(str(other.get("name") or ""))
            if len(name) >= 4 and (name in title or name.replace(" ", "") in title.replace(" ", "")):
                self._push_wrong_project(f"The window in front looks like {other.get('name')}.", other, project, source="rules")
                return

    # --- model assessment ------------------------------------------------------------

    def _maybe_assess(self, window: str) -> None:
        now = self.clock()
        since = now - self._last_assess
        if since < ASSESS_MIN_GAP_S:
            return
        with self.store.lock:
            utt_count = len(self.store.utterances)
            new_cards = any(card.id not in self._seen_cards for card in self.store.cards)
        frame = self.store.latest_frame()
        frame_id = frame.id if frame else None
        gate = self.gate_fn() if self.gate_fn else None
        scene_cut = bool(getattr(gate, "scene_cut", False)) and frame_id != self._assessed_frame
        new_window = bool(window) and window != self._assessed_window
        new_frame = frame_id is not None and frame_id != self._assessed_frame
        new_talk = utt_count > self._assessed_utts
        due = (
            scene_cut
            or new_window
            or new_cards
            or (new_talk and since >= ASSESS_TALK_GAP_S)
            or (new_frame and since >= ASSESS_TALK_GAP_S)
            or ((new_talk or new_frame) and since >= ASSESS_HEARTBEAT_S)
        )
        if not due:
            return
        self._last_assess = now
        self._assessed_utts = utt_count
        self._assessed_window = window
        with self.store.lock:
            self._seen_cards.update(card.id for card in self.store.cards)
        attach = frame if new_frame else None
        self._assessed_frame = frame_id
        try:
            result = self._assess(window, attach)
        except Exception as exc:
            self.store.warn(f"Proactive assessment failed: {exc}")
            return
        self._apply(result, attach)

    def _payload(self, window: str, frame_attached: bool) -> dict[str, Any]:
        ctx = self._context
        with self.store.lock:
            turns = [f"{item.speaker}: {item.text}" for item in self.store.utterances[-30:]]
            cards = [{"type": card.type, "title": card.title, "who": card.who, "when": card.when} for card in self.store.cards[-15:]]
            nudges = [{"kind": n.kind, "title": n.title, "key": n.key, "status": n.status} for n in self.store.nudges[-12:]]
            visuals = [item.caption for item in self.store.snapshots[-8:]]
        event = ctx.get("event") or None
        now = self.now_fn()
        timing: dict[str, Any] = {"listening_min": round((self.clock() - self.started) / 60, 1), "now": now.isoformat(timespec="minutes")}
        end = _parse_time((event or {}).get("end"))
        if end is not None:
            timing["slot_min_left"] = round((end - now).total_seconds() / 60, 1)
        return {
            "active_project": ctx.get("project"),
            "other_projects": ctx.get("other_projects") or [],
            "calendar_slot": {k: (event or {}).get(k) for k in ("title", "start", "end", "attendees")} | {"has_join_link": bool((event or {}).get("meeting_url"))} if event else None,
            "timing": timing,
            "window_in_front": window or "(unknown)",
            "screen": "new frame attached" if frame_attached else f"unchanged since last look: {self._screen_subject or 'nothing seen yet'}",
            "notes_excerpt": _clip(ctx.get("notes") or "", 6000) or "(no notes loaded)",
            "transcript_recent": turns or ["(nothing said yet)"],
            "cards_shown": cards,
            "nudges_shown": nudges,
            "visuals_kept": visuals,
        }

    def _assess(self, window: str, frame: Any) -> dict[str, Any]:
        if self._client is None:
            self._client = httpx.Client(timeout=30)
            self._owns_client = True
        request = build_proactive_request(self.settings.spark_model, self._payload(window, frame is not None), frame.path if frame else None)
        response = self._client.post(
            f"{self.settings.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"},
            json=request,
        )
        response.raise_for_status()
        return parse_proactive_response(response.json())

    def _apply(self, result: dict[str, Any], frame: Any) -> None:
        screen = result.get("screen") or {}
        if screen.get("subject"):
            self._screen_subject = _clip(str(screen["subject"]), 120)
        relevance = str(screen.get("project_relevance") or "unclear")
        confidence = float(screen.get("confidence") or 0)
        mismatch = relevance in {"other_project", "unrelated"} and confidence >= WRONG_PROJECT_CONFIDENCE
        self._mismatch_streak = self._mismatch_streak + 1 if mismatch else 0
        if frame is not None:
            self._keep_visual(result.get("visual") or {}, frame)
        raised_wrong = False
        for raw in result.get("nudges") or []:
            kind = str(raw.get("kind") or "")
            if kind == "WRONG_PROJECT":
                # One confident look is not enough unless it is very sure; two in a row is.
                if not (self._mismatch_streak >= 2 or confidence >= 0.88 or float(raw.get("confidence") or 0) >= 0.9):
                    continue
                raised_wrong = True
            self._push_model(raw)
        if mismatch and not raised_wrong and self._mismatch_streak >= 2:
            # The model judged the screen off-project twice but stayed quiet: speak up with what it saw.
            project = self._context.get("project") or {}
            match_id = screen.get("matching_project_id")
            other = next((p for p in self._context.get("other_projects") or [] if p.get("id") == match_id), None)
            self._push_wrong_project(f"On screen: {self._screen_subject or 'material from another project'}.", other, project, source="model")

    def _keep_visual(self, visual: dict[str, Any], frame: Any) -> None:
        if not visual.get("keep"):
            return
        try:
            image = Image.open(frame.path)
            image.load()
        except Exception:
            return
        digest = dhash(image)
        if any(hamming(digest, seen) < 8 for seen in self._snapshot_hashes):
            return
        self._snapshot_hashes.append(digest)
        caption = _clip(str(visual.get("caption") or "Visual shown in the meeting"), 200)
        self.store.add_snapshot(image, caption=caption, kind=str(visual.get("kind") or "other"))

    def _push_model(self, raw: dict[str, Any]) -> None:
        kind = str(raw.get("kind") or "")
        priority = str(raw.get("priority") or "normal")
        if kind not in NUDGE_KINDS or priority not in NUDGE_PRIORITIES:
            return
        if float(raw.get("confidence") or 0) < MIN_CONFIDENCE:
            return
        title, body = _clip(raw.get("title") or "", 80), _clip(raw.get("body") or "", 320)
        if not title or not body:
            return
        actions = self._clean_actions(raw.get("actions") or [])
        key = _norm(str(raw.get("key") or title))[:60] or _norm(title)
        self._push(kind, priority, title, body, actions, key=f"{kind.lower()}:{key}", source="model", reason=_clip(raw.get("reason") or "", 160))

    def _clean_actions(self, actions: list[Any]) -> list[dict[str, str]]:
        """Only actions Escort can carry out, with values it trusts (its own link, its own project ids)."""
        project_ids = {str(p.get("id")) for p in self._context.get("other_projects") or []}
        url = str((self._context.get("event") or {}).get("meeting_url") or "")
        out: list[dict[str, str]] = []
        for item in actions[:3]:
            if not isinstance(item, dict) or item.get("id") not in ACTION_IDS:
                continue
            label = _clip(item.get("label") or item["id"].replace("_", " ").title(), 24)
            value = str(item.get("value") or "")
            if item["id"] == "open_link":
                if not url:
                    continue
                value = url
            elif item["id"] == "switch_project" and value not in project_ids:
                continue
            elif item["id"] == "ask" and not value.strip():
                continue
            entry = {"id": item["id"], "label": label}
            if value:
                entry["value"] = value
            out.append(entry)
        return out

    def _push_wrong_project(self, seen: str, other: dict[str, Any] | None, project: dict[str, Any], *, source: str) -> None:
        name = project.get("name") or "the active project"
        actions: list[dict[str, str]] = []
        if other:
            actions.append({"id": "switch_project", "label": f"Switch to {_clip(other.get('name') or 'it', 14)}", "value": str(other.get("id"))})
        actions.append({"id": "dismiss", "label": "It's related"})
        body = f"{seen} That doesn't look like {name}. Switch project so notes land in the right place, or bring {name}'s repo or doc on screen."
        self._push("WRONG_PROJECT", "urgent", "This may be the wrong project", body, actions, key=f"wrong_project:{_norm(self._screen_subject or seen)[:40]}", source=source, reason=seen)

    # --- filter + store ------------------------------------------------------------

    def _push(
        self,
        kind: str,
        priority: str,
        title: str,
        body: str,
        actions: list[dict[str, str]],
        *,
        key: str,
        source: str,
        reason: str = "",
    ) -> NudgeRecord | None:
        now = self.clock()
        if self.stop_event.is_set() or key in self._keys:  # nothing lands after the meeting stopped
            return None
        if self._snoozed.get(kind, 0) > now:
            return None
        if kind == "WRONG_PROJECT" and self._snoozed.get("WRONG_PROJECT_RECENT", 0) > now:
            return None  # one wrong-project card per snooze window, whatever its key
        last = self._last_by_priority.get(priority)
        if last is not None and now - last < COOLDOWN_S.get(priority, 30.0) and kind not in {"NO_MEETING", "WRONG_PROJECT"}:
            return None
        self._keys.add(key)
        self._last_by_priority[priority] = now
        if kind == "WRONG_PROJECT":
            self._snoozed["WRONG_PROJECT_RECENT"] = now + DISMISS_SNOOZE_S
        nudge = self.store.add_nudge(
            NudgeRecord(
                id=new_id("ndg"),
                kind=kind,
                priority=priority,
                title=title,
                body=body,
                actions=actions,
                created_at=utc_now(),
                source=source,
                key=key,
                reason=reason,
            )
        )
        if self.on_nudge is not None and not self.stop_event.is_set():
            try:
                self.on_nudge(nudge)
            except Exception as exc:
                log.warning("on_nudge failed: %s", exc)
        return nudge
