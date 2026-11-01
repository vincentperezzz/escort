"""Deterministic chat commands for Escort's own settings, checked before Muse.

Recognises (English and Taglish) requests to pick the watched display, change the
overlay opacity, or hide/show the overlay in screen shares, plus "what monitors do I
have?". A message that is not clearly about a setting returns None and goes to Muse,
so nothing is ever changed from an ordinary question. Every change is confirmed in
the reply and listed in `changed`.

When the user wants to change the watched display without saying which one and more
than one exists, the reply asks and returns a pending question; the caller passes it
back with the next message so a short answer ("2", "the second one", "main") lands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from escort.capture.monitors import ALL, PRIMARY, option_list, primary_of
from escort.desktop.overlay import MAX_OPACITY, MIN_OPACITY, clamp_opacity

PENDING_MONITOR = "watch_monitor"
OPACITY_STEP = 0.15


class SettingsTarget(Protocol):
    """What the intent layer needs from the runtime (DesktopRuntime implements it)."""

    hide_from_share: bool
    overlay_opacity: float

    def monitors_status(self) -> dict[str, Any]: ...

    def set_watch_monitor(self, selected: Any) -> dict[str, Any]: ...

    def set_overlay(self, hide_from_share: bool | None = None, opacity: float | None = None) -> dict[str, Any]: ...

    def overlay_supported(self) -> bool: ...


@dataclass
class SettingsReply:
    reply: str
    changed: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] | None = None


# --- vocabulary --------------------------------------------------------------------------

_SHARE = r"(?:screen[\s-]*shar\w*|screenshar\w*|\bshar(?:e|es|ed|ing)\b|recordings?|screenshots?)"
_MONITOR_NOUN = r"\b(?:monitors?|screens?|displays?|iskrin)\b"
_VERB = (
    r"\b(?:watch|watching|use|using|switch|change|move|go|look|set|select|pick|choose|capture|track|follow|"
    r"focus|point|bantayan|binabantayan|tingnan|tignan|gamitin|lipat|ilipat|palitan|panoorin|i-?watch|"
    r"i-?monitor|i-?switch|i-?change)\b"
)
_SWITCH = r"\b(?:switch|change|swap|different|another|other|lipat|ilipat|palitan|iba|ibang|kabila)\b"
_ORDINALS = {
    1: r"first|1st|una|unang|ika-?1|ika-?isa",
    2: r"second|2nd|pangalawa|pangalawang|ikalawa|ikalawang|ika-?2",
    3: r"third|3rd|pangatlo|pangatlong|ikatlo|ikatlong|ika-?3",
    4: r"fourth|4th|pang-?apat|pang-?apat na|ikaapat|ika-?4",
}
_PRIMARY = r"\b(?:main|primary|default|pangunahin\w*|laptop)\b"
_ALL = r"\b(?:all|every|both|lahat|dalawa|everything)\b"
_CANCEL = r"^(?:cancel|never ?mind|nvm|wag na|huwag na|no|nope|keep it|stay|ok lang|okay lang|wala)\b"
_OVERLAY_SUBJECT = r"\b(?:overlay|escort|it|yourself|window|panel|app|sarili mo|ikaw)\b"
_ADVICE = r"^(?:should i|do i|how (?:do|can|should) i|paano|dapat ba)\b"


def _norm(message: str) -> str:
    text = message.lower().replace("’", "'").replace("‘", "'").replace("—", " ").replace("–", " ")
    text = re.sub(r"[!?.,;:()\"]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _words(text: str) -> int:
    return len(text.split())


# --- monitor ----------------------------------------------------------------------------


def _number_target(text: str, *, bare: bool) -> int | None:
    match = re.search(r"\b(?:monitor|screen|display|iskrin|number|no|#)\s*#?\s*(\d{1,2})\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:monitor|screen|display)", text)
    if match:
        return int(match.group(1))
    for number, pattern in _ORDINALS.items():
        if re.search(rf"\b(?:{pattern})\b", text):
            return number
    if bare:
        match = re.fullmatch(r"(?:yung |ang |the |number |no |#)?(\d{1,2})(?: one| po| na lang| please)?", text)
        if match:
            return int(match.group(1))
    return None


def _monitor_target(text: str, monitors: list[dict[str, Any]], current: Any, *, bare: bool = False) -> Any:
    """'primary' | 'all' | int | None, from what the user said."""
    number = _number_target(text, bare=bare)
    if number is not None:
        return number
    if re.search(_ALL, text) and (bare or re.search(r"\b(?:monitors|screens|displays)\b", text) or re.search(r"\b(?:both|lahat)\b", text)):
        return ALL
    if re.search(_PRIMARY, text):
        return PRIMARY
    if re.search(r"\b(?:other|kabila|isa pa|yung isa|ibang)\b", text) and len(monitors) == 2:
        current_index = current if isinstance(current, int) else (primary_of(monitors) or {}).get("index", 1)
        return next((item["index"] for item in monitors if item["index"] != current_index), None)
    return None


def _monitor_name(choice: Any, monitors: list[dict[str, Any]]) -> str:
    if choice == ALL:
        return "all displays" if len(monitors) > 1 else (primary_of(monitors) or {}).get("name", "Display 1")
    if choice == PRIMARY:
        primary = primary_of(monitors)
        return f"{primary['name']}, your main display" if primary else "your main display"
    match = next((item for item in monitors if item["index"] == choice), None)
    return match["name"] if match else f"Display {choice}"


def _question(monitors: list[dict[str, Any]], lead: str = "Which screen should I watch?") -> SettingsReply:
    options = option_list(monitors)
    return SettingsReply(
        reply=f"{lead}\n{options}\nYou can also say \"all\" to watch every screen.",
        pending={"kind": PENDING_MONITOR},
    )


def _apply_monitor(target: SettingsTarget, choice: Any, monitors: list[dict[str, Any]]) -> SettingsReply:
    if isinstance(choice, int) and monitors and not any(item["index"] == choice for item in monitors):
        count = len(monitors)
        lead = f"I only see {count} display{'s' if count != 1 else ''}, so there's no Display {choice}."
        if count == 1:
            return SettingsReply(reply=f"{lead} I'm watching {monitors[0]['name']}.")
        return _question(monitors, lead=f"{lead} Which one should I watch?")
    before = target.monitors_status()
    if before.get("selected") == choice and not before.get("notice"):
        return SettingsReply(reply=f"I'm already watching {_monitor_name(choice, monitors)}.")
    after = target.set_watch_monitor(choice)
    label = (after.get("effective") or {}).get("label") or _monitor_name(choice, monitors)
    name = _monitor_name(choice, monitors)
    return SettingsReply(
        reply=f"Done, I'm watching {name} now.",
        changed=[{"setting": "watch_monitor", "value": after.get("selected", choice), "label": f"Watching {label}"}],
    )


def _list_monitors(target: SettingsTarget, monitors: list[dict[str, Any]]) -> SettingsReply:
    status = target.monitors_status()
    effective = (status.get("effective") or {}).get("label") or "your main display"
    if not monitors:
        return SettingsReply(reply=f"I can't list your displays right now. I'm watching {effective}.")
    if len(monitors) == 1:
        return SettingsReply(reply=f"You have one display, {monitors[0]['label']}, and I'm watching it.")
    lines = "\n".join(
        f"{item['index']} · {item['label']}{' (primary)' if item['primary'] else ''}" for item in monitors
    )
    return SettingsReply(
        reply=f"You have {len(monitors)} displays:\n{lines}\nI'm watching {effective}. Tell me a number, or \"all\", to switch.",
        pending={"kind": PENDING_MONITOR},
    )


def _monitor_intent(text: str, target: SettingsTarget, monitors: list[dict[str, Any]]) -> SettingsReply | None:
    cleaned = re.sub(_SHARE, " ", text)
    if not re.search(_MONITOR_NOUN, cleaned):
        return None
    asks_list = re.search(
        r"\b(?:what|which|list|show me|how many|ilan|ilang|ano|anong|available|connected|do i have|meron)\b", cleaned
    ) and (re.search(r"\b(?:monitors|screens|displays)\b", cleaned) or re.search(r"\bdo i have\b|\bmeron\b|\bilan", cleaned))
    asks_current = re.search(
        r"\b(?:which|what|anong|aling)\s+(?:monitor|screen|display|iskrin)\b.*\b(?:watch\w*|look\w*|using|binabantayan|tinitingnan)\b",
        cleaned,
    )
    current = target.monitors_status().get("selected")
    choice = _monitor_target(cleaned, monitors, current)
    has_verb = bool(re.search(_VERB, cleaned))
    explicit = bool(re.search(r"\b(?:monitor|screen|display)\s*#?\s*\d", cleaned))
    question = re.match(r"^(?:what|what's|whats|which|why|how|is|are|does|do|ano|anong|aling|bakit|paano)\b", cleaned)
    if choice is not None and (has_verb or explicit or _words(cleaned) <= 4):
        if question and not has_verb:
            return _list_monitors(target, monitors)
        return _apply_monitor(target, choice, monitors)
    wants_switch = (re.search(_SWITCH, cleaned) and has_verb) or re.search(r"\b(?:switch|change|palitan|lipat|ilipat)\b", cleaned)
    if wants_switch and len(monitors) > 1:
        return _question(monitors)
    if wants_switch and len(monitors) == 1:
        return SettingsReply(reply=f"You have one display, {monitors[0]['label']}, and I'm already watching it.")
    if asks_current or asks_list:
        return _list_monitors(target, monitors)
    return None


def _resolve_pending(text: str, target: SettingsTarget, monitors: list[dict[str, Any]]) -> SettingsReply | None:
    if _words(text) > 6:
        return None
    if re.search(_CANCEL, text):
        label = (target.monitors_status().get("effective") or {}).get("label") or "the same display"
        return SettingsReply(reply=f"OK, I'll keep watching {label}.")
    choice = _monitor_target(text, monitors, target.monitors_status().get("selected"), bare=True)
    if choice is None:
        return None
    return _apply_monitor(target, choice, monitors)


# --- overlay ----------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _opacity_intent(text: str, target: SettingsTarget) -> SettingsReply | None:
    if re.search(_ADVICE, text):
        return None
    mentions_opacity = re.search(r"\b(?:opacity|opaque)\b", text)
    mentions_transparent = re.search(r"\b(?:transparen\w*|see[\s-]?through|translucent|malinaw)\b", text)
    mentions_overlay = re.search(r"\b(?:overlay|window|panel)\b", text) or re.search(r"^(?:make it|gawin mo?ng?)\b", text)
    if not (mentions_opacity or (mentions_transparent and mentions_overlay)):
        return None
    current = target.overlay_opacity
    requested: float | None = None
    percent = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|porsyento|pct)", text) or (
        re.search(r"\b(?:opacity|opaque|transparen\w*)\b\D{0,12}(\d{1,3})\b", text)
    )
    if percent:
        number = float(percent.group(1)) / 100
        requested = 1 - number if mentions_transparent and not mentions_opacity else number
    elif re.search(r"\b(?:fully|completely|totally|100)\s*(?:opaque|solid)\b|\bnot transparent\b|\bno transparency\b", text):
        requested = MAX_OPACITY
    elif re.search(r"\b(?:less|di gaanong|hindi gaanong|bawasan ang)\s+(?:transparen\w*|see[\s-]?through)|\bmore (?:opaque|solid|visible)|\bmas (?:solid|opaque|kita)|\bdarker\b|\bincrease (?:the )?opacity|\bopacity up\b", text):
        requested = current + OPACITY_STEP
    elif re.search(r"\b(?:more|mas|extra|lalo)\s+(?:transparen\w*|see[\s-]?through|translucent|malinaw)|\bless (?:opaque|solid|visible)|\bfainter\b|\blighter\b|\b(?:lower|reduce|decrease|bawasan) (?:the )?opacity|\bopacity down\b", text):
        requested = current - OPACITY_STEP
    if requested is None:
        return None
    value = clamp_opacity(requested)
    if value == clamp_opacity(current):
        edge = " That's as far as it goes." if value in (MIN_OPACITY, MAX_OPACITY) else ""
        return SettingsReply(reply=f"The overlay is already at {_pct(value)} opacity.{edge}")
    target.set_overlay(opacity=value)
    note = ""
    if requested < MIN_OPACITY:
        note = f" {_pct(MIN_OPACITY)} is as see-through as it goes, so text stays readable."
    return SettingsReply(
        reply=f"Done, the overlay is at {_pct(value)} opacity now.{note}",
        changed=[{"setting": "overlay_opacity", "value": value, "label": f"Overlay {_pct(value)} opacity"}],
    )


def _share_intent(text: str, target: SettingsTarget) -> SettingsReply | None:
    if not re.search(_SHARE, text) or re.search(_ADVICE, text):
        return None
    show = re.search(
        r"\b(?:unhide|stop hiding|don'?t hide|do not hide|wag (?:mo )?(?:nang )?itago|huwag (?:mo )?itago|ipakita|"
        r"show|visible|include|let (?:them|others|people|everyone) see)\b",
        text,
    )
    hide = re.search(r"\b(?:hide|itago|tago|hidden|invisible|exclude|keep \w+ out|leave \w+ out)\b", text)
    imperative = re.match(r"^(?:please |pls |can you |could you |pakí|paki)?(?:hide|show|unhide|stop|don'?t|itago|ipakita|wag|huwag|keep|leave|make)\b", text)
    if not (re.search(_OVERLAY_SUBJECT, text) or imperative):
        return None
    if show:
        wanted = False
    elif hide:
        wanted = True
    else:
        return None
    if target.hide_from_share == wanted:
        state = "hidden from" if wanted else "visible in"
        return SettingsReply(reply=f"The overlay is already {state} screen shares.")
    target.set_overlay(hide_from_share=wanted)
    if wanted:
        reply = "Done, the overlay is hidden from screen shares and recordings now. You still see it."
        if not target.overlay_supported():
            reply += " This Windows build can't hide windows from capture, so it may still show up."
        label = "Hidden from screen share"
    else:
        reply = "Done, the overlay now shows up in screen shares and recordings."
        label = "Visible in screen share"
    return SettingsReply(reply=reply, changed=[{"setting": "hide_from_share", "value": wanted, "label": label}])


# --- entry point ------------------------------------------------------------------------


def handle_settings_message(
    message: str, target: SettingsTarget, pending: dict[str, Any] | None = None
) -> SettingsReply | None:
    """A reply when the message is about Escort's settings, else None (send it to Muse)."""
    text = _norm(message)
    if not text:
        return None
    monitors = list(target.monitors_status().get("monitors") or [])
    if pending and pending.get("kind") == PENDING_MONITOR:
        answered = _resolve_pending(text, target, monitors)
        if answered is not None:
            return answered
    replies = [
        reply
        for reply in (
            _monitor_intent(text, target, monitors),
            _opacity_intent(text, target),
            _share_intent(text, target),
        )
        if reply is not None
    ]
    if not replies:
        return None
    pending_out = next((reply.pending for reply in replies if reply.pending), None)
    return SettingsReply(
        reply=" ".join(reply.reply for reply in replies) if len(replies) > 1 else replies[0].reply,
        changed=[item for reply in replies for item in reply.changed],
        pending=pending_out,
    )


def describe_settings(target: SettingsTarget) -> str:
    """One paragraph for Muse's system prompt: what it can tell the user about settings."""
    status = target.monitors_status()
    monitors = status.get("monitors") or []
    effective = (status.get("effective") or {}).get("label") or "the main display"
    names = ", ".join(
        f"{item['label']}{' (primary)' if item['primary'] else ''}" for item in monitors
    ) or "unknown"
    return (
        "Escort settings the user can change by asking you in plain words (Settings > General has the same controls): "
        f"(1) which screen Escort watches: now {effective}; displays: {names}; say e.g. 'watch monitor 2', "
        "'use the main screen' or 'watch all screens'. "
        f"(2) overlay opacity, now {_pct(target.overlay_opacity)}, from 30% to 100%; e.g. 'overlay 60% opacity' or "
        "'make the overlay more transparent'. "
        f"(3) hiding the overlay from screen share, now {'on' if target.hide_from_share else 'off'}; e.g. "
        "'hide the overlay from screen share' or 'show it in the share'. "
        "Those requests are handled before you see them, so if you get one it was unclear: say which phrase works. "
        "Never claim you changed a setting yourself."
    )
