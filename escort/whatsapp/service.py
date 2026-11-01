from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from escort.models import CardRecord
from escort.prd import parse_sections
from escort.store import MeetingStore, new_id, utc_now
from escort.whatsapp.brain import ask_meeting
from escort.whatsapp.config import WhatsAppConfig, digits
from escort.whatsapp.extract import extract_text
from escort.whatsapp.graph import GraphClient
from escort.whatsapp.inbound import Inbound, parse_messages
from escort.whatsapp.render import buttons_for, card_body, crop_file, list_rows
from escort.whatsapp.signature import signature_ok

WINDOW = timedelta(hours=24)


class PhoneSession:
    def __init__(self, number: str) -> None:
        self.number = number
        self.window_until: datetime | None = None
        self.meeting_open = False
        self.prd_text = ""
        self.notes: list[str] = []
        self.queue: list[dict] = []
        self.sent: set[str] = set()
        self.known: dict[str, dict] = {}


class WhatsAppService:
    def __init__(
        self,
        config: WhatsAppConfig,
        graph: GraphClient,
        store: MeetingStore,
        model_http: httpx.Client | None = None,
        state_http: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.graph = graph
        self.store = store
        self.model_http = model_http
        self.state_http = state_http or httpx.Client(timeout=5)
        self.sessions: dict[str, PhoneSession] = {}

    def verify(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if mode == "subscribe" and token and token == self.config.verify_token and challenge is not None:
            return challenge
        return None

    def signature_ok(self, body: bytes, header: str | None) -> bool:
        return signature_ok(self.config.app_secret, body, header)

    def handle_payload(self, payload: dict) -> None:
        for inbound in parse_messages(payload):
            self.handle_inbound(inbound)

    def handle_inbound(self, inbound: Inbound) -> None:
        number = digits(inbound.sender)
        if not self.config.allows(number):
            return
        session = self.sessions.setdefault(number, PhoneSession(number))
        session.window_until = datetime.now(timezone.utc) + WINDOW
        if inbound.kind == "action":
            self._action(session, inbound.action_id, inbound.text)
            self._flush(session)
            return
        if inbound.kind == "document":
            self._document(session, inbound)
            self._flush(session)
            return
        if inbound.kind == "image":
            self._image(session, inbound)
            self._flush(session)
            return
        self._text(session, inbound.text.strip())
        self._flush(session)

    def fanout(self, cards: list[dict]) -> None:
        fresh = []
        for card in cards:
            if card.get("type") not in {"PRD_CONFLICT", "COMMITMENT", "SUGGESTED_REPLY"}:
                continue
            card_id = str(card.get("id") or "")
            if not card_id:
                continue
            fresh.append(card)
        if not fresh:
            return
        real = [session for session in self.sessions.values() if session.number != "_queued"]
        targets = [session for session in real if self._window_open(session)]
        if not targets:
            if not real:
                holder = self.sessions.setdefault("_queued", PhoneSession("_queued"))
                for card in fresh:
                    holder.known[str(card.get("id"))] = card
                    if not any(str(queued.get("id")) == str(card.get("id")) for queued in holder.queue):
                        holder.queue.append(card)
                return
            for session in real:
                for card in fresh:
                    session.known[str(card.get("id"))] = card
                    if str(card.get("id")) not in session.sent and not any(
                        str(queued.get("id")) == str(card.get("id")) for queued in session.queue
                    ):
                        session.queue.append(card)
            return
        for session in targets:
            for card in fresh:
                session.known[str(card.get("id"))] = card
            self._deliver(session, [card for card in fresh if str(card.get("id")) not in session.sent])

    def pull_remote_cards(self) -> list[dict]:
        if not self.config.state_url:
            return []
        try:
            response = self.state_http.get(self.config.state_url)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        return list(payload.get("cards") or [])

    def _text(self, session: PhoneSession, text: str) -> None:
        command = " ".join(text.lower().split())
        if command == "start meeting":
            session.meeting_open = True
            self.graph.send_text(
                session.number,
                "Meeting's open. This text starts WhatsApp's 24-hour customer-service window, "
                "so I can push conflict cards, commitments, and lines to say until it closes. "
                "Send `catch me up`, `recap`, or `end meeting` anytime. Anything else, I ask Muse.",
            )
            return
        if command in {"catch me up", "recap"}:
            self.graph.send_text(session.number, self._recap(session))
            return
        if command == "end meeting":
            session.meeting_open = False
            self.graph.send_text(session.number, self._end_text(session))
            return
        if not session.meeting_open:
            self.graph.send_text(
                session.number,
                "Send `start meeting` to open the session. "
                "That message is what opens the 24-hour window I need to text you cards.",
            )
            return
        answer = ask_meeting(self.config, text, self._context(session), self.model_http)
        self.graph.send_text(session.number, answer)

    def _action(self, session: PhoneSession, action_id: str, title: str) -> None:
        kind, _, card_id = action_id.partition("|")
        card = session.known.get(card_id, {})
        if kind == "note":
            self._add_commitment(card, title or "Noted from WhatsApp")
            self.graph.send_text(session.number, f"Noted. I'll keep that as a commitment: {card.get('title') or title}.")
            return
        if kind == "ignore":
            session.notes.append(f"Ignored {card.get('title') or card_id}")
            self.graph.send_text(session.number, "Ignored. I won't bring that one back.")
            return
        if kind == "push":
            session.notes.append(f"Push back on {card.get('title') or card_id}")
            line = card.get("body") or "Sige, hindi pa natin i-commit yan."
            self.graph.send_text(session.number, f"Push back. Say this: {line}")
            return
        if kind == "say":
            session.notes.append(f"Will say: {title or card.get('body') or card_id}")
            self.graph.send_text(session.number, f"Locked. Say: {title or card.get('body') or 'that line'}.")
            return
        self.graph.send_text(session.number, "I didn't recognize that button.")

    def _document(self, session: PhoneSession, inbound: Inbound) -> None:
        _name, data = self.graph.download_media(inbound.media_id)
        filename = inbound.filename or _name
        try:
            text = extract_text(filename, data)
        except ValueError:
            self.graph.send_text(session.number, "I saved that file, but I can only read pdf, md, docx, and txt.")
            return
        if not text:
            self.graph.send_text(session.number, "That document had no text I could use as scope.")
            return
        session.prd_text = text
        self.store.prd_sections = [
            {"anchor": section.anchor, "heading": section.heading, "quote": section.quote()}
            for section in parse_sections(text)
        ]
        self.store.prd_name = filename
        session.notes.append(f"PRD set from {filename}")
        self.graph.send_text(session.number, f"Got {filename}. That's the PRD scope for this meeting.")

    def _image(self, session: PhoneSession, inbound: Inbound) -> None:
        filename, data = self.graph.download_media(inbound.media_id)
        suffix = ".jpg"
        if "png" in inbound.mime:
            suffix = ".png"
        path = self.store.media_dir / f"wa_{inbound.media_id}{suffix}"
        path.write_bytes(data)
        caption = inbound.text or filename or path.name
        session.notes.append(f"Image context: {caption} ({path.name})")
        self.graph.send_text(session.number, "Saved that image as meeting context.")

    def _flush(self, session: PhoneSession) -> None:
        if not self._window_open(session):
            return
        waiting = list(session.queue)
        session.queue.clear()
        holder = self.sessions.get("_queued")
        if holder is not None and holder is not session:
            for card in list(holder.queue):
                card_id = str(card.get("id"))
                if card_id not in session.sent:
                    waiting.append(card)
            holder.queue.clear()
        if not waiting:
            return
        if waiting and session.meeting_open:
            self.graph.send_text(
                session.number,
                f"I was holding {len(waiting)} card{'s' if len(waiting) != 1 else ''} until this window opened.",
            )
        self._deliver(session, waiting)

    def _deliver(self, session: PhoneSession, cards: list[dict]) -> None:
        replies = [card for card in cards if card.get("type") == "SUGGESTED_REPLY"]
        others = [card for card in cards if card.get("type") != "SUGGESTED_REPLY"]
        for card in others:
            card_id = str(card.get("id"))
            if card_id in session.sent:
                continue
            session.known[card_id] = card
            image = crop_file(card, self.store.media_dir)
            if image is not None:
                media_id = self.graph.upload_media(image)
                self.graph.send_image(session.number, media_id, card.get("title") or "Screen crop")
            self.graph.send_buttons(session.number, card_body(card), buttons_for(card))
            session.sent.add(card_id)
        pending = []
        for card in replies:
            card_id = str(card.get("id"))
            if card_id in session.sent:
                continue
            session.known[card_id] = card
            pending.append(card)
            session.sent.add(card_id)
        if pending:
            self.graph.send_list(
                session.number,
                "Lines you can say. Pick one.",
                list_rows(pending),
            )

    def _window_open(self, session: PhoneSession) -> bool:
        return bool(session.window_until and session.window_until > datetime.now(timezone.utc))

    def _add_commitment(self, card: dict, title: str) -> None:
        self.store.add_card(
            CardRecord(
                id=new_id("card"),
                type="COMMITMENT",
                title="Noted on WhatsApp",
                body=card.get("body") or title,
                prd_section=card.get("prd_section"),
                who="Me",
                what=card.get("title") or title,
                utterance_id=None,
                trigger_text=title,
                created_at=utc_now(),
                latency_ms=0,
                crop_path=None,
                crop_url=None,
                source="whatsapp",
                deictic=None,
                segmenter=None,
                when="this meeting",
                proactive=False,
            )
        )

    def _recap(self, session: PhoneSession) -> str:
        snapshot = self._snapshot()
        meeting = snapshot.get("meeting") or {}
        lines = ["Catch-up:"]
        opens = meeting.get("open_items") or []
        if opens:
            lines.append("Open against the PRD:")
            lines.extend(f"- {item.get('title')} ({item.get('prd_section') or 'PRD'})" for item in opens)
        commitments = meeting.get("commitments") or []
        if commitments:
            lines.append("Promises:")
            lines.extend(
                f"- {item.get('who')} will {item.get('what')} ({item.get('when')})" for item in commitments
            )
        if session.notes:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in session.notes[-5:])
        if len(lines) == 1:
            lines.append("Nothing open yet.")
        return "\n".join(lines)

    def _end_text(self, session: PhoneSession) -> str:
        snapshot = self._snapshot()
        commitments = (snapshot.get("meeting") or {}).get("commitments") or []
        lines = ["Meeting closed. Commitments:"]
        if commitments:
            lines.extend(
                f"- {item.get('who')} will {item.get('what')} ({item.get('when')})" for item in commitments
            )
        else:
            lines.append("- None on the table.")
        until = session.window_until.isoformat(timespec="minutes") if session.window_until else "unknown"
        lines.append(
            f"WhatsApp still lets me text you until {until}, because the 24-hour window follows your last message. "
            "Send `start meeting` next time."
        )
        return "\n".join(lines)

    def _context(self, session: PhoneSession) -> str:
        snapshot = self._snapshot()
        if session.prd_text:
            snapshot = dict(snapshot)
            snapshot["uploaded_prd"] = session.prd_text[:8000]
        if session.notes:
            snapshot = dict(snapshot)
            snapshot["notes"] = session.notes
        return json.dumps(snapshot, ensure_ascii=False)[:12000]

    def _snapshot(self) -> dict:
        local = self.store.snapshot()
        if not self.config.state_url:
            return local
        try:
            response = self.state_http.get(self.config.state_url)
            response.raise_for_status()
            remote = response.json()
        except Exception:
            return local
        if session_prd_richer(local, remote):
            remote = dict(remote)
            remote["prd_name"] = local.get("prd_name")
            remote.setdefault("meeting", {})
            remote["meeting"]["prd_sections"] = local.get("meeting", {}).get("prd_sections")
        local_commitments = (local.get("meeting") or {}).get("commitments") or []
        if local_commitments:
            remote = dict(remote)
            meeting = dict(remote.get("meeting") or {})
            meeting["commitments"] = list(meeting.get("commitments") or []) + local_commitments
            remote["meeting"] = meeting
        return remote


def session_prd_richer(local: dict, remote: dict) -> bool:
    local_sections = (local.get("meeting") or {}).get("prd_sections") or []
    remote_sections = (remote.get("meeting") or {}).get("prd_sections") or []
    return bool(local_sections) and not remote_sections
