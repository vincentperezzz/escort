"""Save meeting notes to Google Docs, limited to docs Escort itself created.

Needs the optional WRITE_SCOPES (drive.file + documents), granted incrementally when the
user turns on "Save notes to Drive". drive.file only reaches files this app created, and
on top of that every write is checked against Escort's own registry: a doc id Escort did
not create is refused before any request is made.

Layout: one folder named "Escort" (id remembered in prefs), one Google Doc per meeting
titled "Escort · <meeting title> · <YYYY-MM-DD>". Writes only ever append.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from escort.context.sources.base import ConnectorAuthError, ConnectorError, ConnectorScopeError
from escort.context.sources.google_auth import PROVIDER, WRITE_SCOPES, GoogleAuth
from escort.store import utc_now

log = logging.getLogger("escort.google.docs")

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DOCS_URL = "https://docs.googleapis.com/v1/documents"
DOC_LINK = "https://docs.google.com/document/d/{id}/edit"
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
FOLDER_NAME = "Escort"
APP_PROPERTY = {"escort": "notes"}
TITLE_SEP = " · "
SECTION_HEADINGS = ("Summary", "Commitments", "Conflicts")


class UnknownDocError(ConnectorError):
    """A write targeted a doc Escort did not create; refused without calling Google."""

    status_code = 403
    code = "unknown_doc"


class NotesState(Protocol):
    """Persistent state: the folder id and the meeting_id -> doc registry (prefs-backed)."""

    def folder_id(self) -> str | None: ...

    def set_folder_id(self, folder_id: str | None) -> None: ...

    def docs(self) -> dict[str, dict[str, Any]]: ...

    def set_docs(self, docs: dict[str, dict[str, Any]]) -> None: ...


@dataclass
class DictNotesState:
    """In-memory NotesState over a plain dict (prefs, or a test dict)."""

    data: dict[str, Any]
    on_change: Callable[[], None] = lambda: None

    def folder_id(self) -> str | None:
        return self.data.get("google_notes_folder_id") or None

    def set_folder_id(self, folder_id: str | None) -> None:
        if folder_id:
            self.data["google_notes_folder_id"] = folder_id
        else:
            self.data.pop("google_notes_folder_id", None)
        self.on_change()

    def docs(self) -> dict[str, dict[str, Any]]:
        docs = self.data.get("google_notes_docs")
        return dict(docs) if isinstance(docs, dict) else {}

    def set_docs(self, docs: dict[str, dict[str, Any]]) -> None:
        self.data["google_notes_docs"] = docs
        self.on_change()


def doc_title(meeting_title: str, when: datetime | None = None) -> str:
    title = " ".join((meeting_title or "").split()) or "Meeting"
    day = (when or datetime.now()).strftime("%Y-%m-%d")
    return f"Escort{TITLE_SEP}{title[:120]}{TITLE_SEP}{day}"


def commitment_line(who: str, what: str, due: str | None) -> str:
    return f"- {who or 'Someone'}: {what} ({due or 'unspecified'})"


def _units(text: str) -> int:
    """Docs API indexes count UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def summary_blocks(summary: dict[str, Any], stamp: str) -> list[tuple[str, str]]:
    """[(paragraph text, style)] for one dated recap section. Style is a Docs namedStyleType."""
    blocks: list[tuple[str, str]] = [(f"Recap{TITLE_SEP}{stamp}", "HEADING_1")]
    blocks.append(("Summary", "HEADING_2"))
    bullets = [str(item).strip() for item in summary.get("bullets") or [] if str(item).strip()]
    blocks.extend((f"- {item}", "NORMAL_TEXT") for item in bullets or ["Nothing notable."])
    notes = [topic for topic in summary.get("notes") or [] if isinstance(topic, dict) and topic.get("points")]
    if notes:
        blocks.append(("Notes", "HEADING_2"))
        for topic in notes:
            blocks.append((str(topic.get("heading") or "Notes"), "HEADING_3"))
            for point in topic["points"]:
                blocks.append((f"- {point.get('text')}", "NORMAL_TEXT"))
                blocks.extend((f"    - {sub}", "NORMAL_TEXT") for sub in point.get("sub") or [])
    for key, heading in (("takeaways", "Key takeaways"), ("decisions", "Decisions"), ("open_questions", "Open questions")):
        items = [str(item).strip() for item in summary.get(key) or [] if str(item).strip()]
        if items:
            blocks.append((heading, "HEADING_2"))
            blocks.extend((f"- {item}", "NORMAL_TEXT") for item in items)
    blocks.append(("Commitments", "HEADING_2"))
    commitments = [item for item in summary.get("commitments") or [] if isinstance(item, dict) and item.get("what")]
    blocks.extend(
        (commitment_line(str(item.get("who") or ""), str(item["what"]), str(item.get("due") or "")), "NORMAL_TEXT")
        for item in commitments
    )
    if not commitments:
        blocks.append(("No commitments captured.", "NORMAL_TEXT"))
    blocks.append(("Conflicts", "HEADING_2"))
    conflicts = [item for item in summary.get("conflicts") or [] if isinstance(item, dict) and item.get("title")]
    for item in conflicts:
        section = str(item.get("section") or "")
        blocks.append((f"- {item['title']}" + (f" ({section})" if section else ""), "NORMAL_TEXT"))
    if not conflicts:
        blocks.append(("No conflicts against your notes.", "NORMAL_TEXT"))
    return blocks


def structured_requests(blocks: list[tuple[str, str]], start: int) -> list[dict[str, Any]]:
    """insertText at the end of the body, then a paragraph style per block (NORMAL first)."""
    text = "".join(f"{line}\n" for line, _style in blocks)
    requests: list[dict[str, Any]] = [
        {"insertText": {"endOfSegmentLocation": {"segmentId": ""}, "text": text}},
        {
            "updateParagraphStyle": {
                "range": {"startIndex": start, "endIndex": start + _units(text)},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }
        },
    ]
    cursor = start
    for line, style in blocks:
        length = _units(line) + 1
        if style != "NORMAL_TEXT":
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": cursor, "endIndex": cursor + length},
                        "paragraphStyle": {"namedStyleType": style},
                        "fields": "namedStyleType",
                    }
                }
            )
        cursor += length
    return requests


class GoogleNotes:
    def __init__(self, auth: GoogleAuth, state: NotesState) -> None:
        self.auth = auth
        self.state = state

    # ---- guards ---------------------------------------------------------
    def require_write(self) -> None:
        if not self.auth.connected():
            raise ConnectorAuthError(PROVIDER, "Connect Google first, then turn on Save notes to Drive.", code="not_connected")
        if not self.auth.has_scopes(WRITE_SCOPES):
            raise ConnectorScopeError(PROVIDER, "Escort needs your permission to save notes to Google Docs. Click Allow note saving.")

    def _known(self, doc_id: str) -> dict[str, Any]:
        for entry in self.state.docs().values():
            if entry.get("doc_id") == doc_id:
                return entry
        raise UnknownDocError(PROVIDER, "Escort only edits Google Docs it created; that document is not one of them.")

    # ---- Drive ----------------------------------------------------------
    def ensure_folder(self) -> str:
        """The 'Escort' folder: the remembered id if it still exists, else one this app made, else a new one."""
        self.require_write()
        remembered = self.state.folder_id()
        if remembered:
            try:
                meta = self.auth.request(
                    "GET", f"{DRIVE_FILES_URL}/{remembered}", params={"fields": "id,trashed,mimeType"}
                ).json()
                if not meta.get("trashed") and meta.get("mimeType", FOLDER_MIME) == FOLDER_MIME:
                    return remembered
            except ConnectorAuthError:
                raise
            except ConnectorError as err:
                if err.code != "not_found":  # scope, API disabled, outage: surface it, never duplicate
                    raise
        found = self.auth.request(
            "GET",
            DRIVE_FILES_URL,
            params={
                "q": (
                    f"mimeType = '{FOLDER_MIME}' and name = '{FOLDER_NAME}' and trashed = false "
                    "and appProperties has { key='escort' and value='notes' }"
                ),
                "fields": "files(id,name)",
                "spaces": "drive",
                "pageSize": "1",
            },
        ).json()
        files = found.get("files") or []
        if files:
            folder_id = str(files[0]["id"])
        else:
            created = self.auth.request(
                "POST",
                DRIVE_FILES_URL,
                params={"fields": "id"},
                json={"name": FOLDER_NAME, "mimeType": FOLDER_MIME, "appProperties": APP_PROPERTY},
            ).json()
            folder_id = str(created["id"])
            log.info("Created the Escort notes folder in Drive")
        self.state.set_folder_id(folder_id)
        return folder_id

    def create_doc(self, title: str, folder_id: str) -> dict[str, Any]:
        """A new, empty Google Doc inside the folder (Drive files.create with the Docs mime type)."""
        self.require_write()
        created = self.auth.request(
            "POST",
            DRIVE_FILES_URL,
            params={"fields": "id,name,webViewLink"},
            json={"name": title, "mimeType": DOC_MIME, "parents": [folder_id], "appProperties": APP_PROPERTY},
        ).json()
        doc_id = str(created["id"])
        return {
            "doc_id": doc_id,
            "url": str(created.get("webViewLink") or DOC_LINK.format(id=doc_id)),
            "title": str(created.get("name") or title),
            "created_at": utc_now(),
        }

    # ---- Docs -----------------------------------------------------------
    def append_text(self, doc_id: str, text: str) -> None:
        """Append plain text at the end of the body. Refuses docs Escort did not create."""
        self._known(doc_id)
        self.require_write()
        body = text if text.endswith("\n") else f"{text}\n"
        self.auth.request(
            "POST",
            f"{DOCS_URL}/{doc_id}:batchUpdate",
            json={"requests": [{"insertText": {"endOfSegmentLocation": {"segmentId": ""}, "text": body}}]},
        )

    def append_structured(self, doc_id: str, blocks: list[tuple[str, str]]) -> None:
        """Append headed paragraphs (Summary / Commitments / Conflicts) at the end of the body."""
        self._known(doc_id)
        self.require_write()
        doc = self.auth.request("GET", f"{DOCS_URL}/{doc_id}", params={"fields": "body.content(endIndex)"}).json()
        content = (doc.get("body") or {}).get("content") or [{}]
        end_index = int(content[-1].get("endIndex") or 2)
        # endOfSegmentLocation inserts before the body's final newline, i.e. at end_index - 1.
        requests = structured_requests(blocks, max(1, end_index - 1))
        self.auth.request("POST", f"{DOCS_URL}/{doc_id}:batchUpdate", json={"requests": requests})

    # ---- per-meeting docs ------------------------------------------------
    def doc_for(self, meeting_id: str) -> dict[str, Any] | None:
        entry = self.state.docs().get(meeting_id)
        return dict(entry) if entry else None

    def ensure_meeting_doc(self, meeting_id: str, meeting_title: str) -> dict[str, Any]:
        existing = self.doc_for(meeting_id)
        if existing:
            return existing
        folder_id = self.ensure_folder()
        entry = {**self.create_doc(doc_title(meeting_title), folder_id), "meeting_id": meeting_id}
        docs = self.state.docs()
        docs[meeting_id] = entry
        self.state.set_docs(docs)
        log.info("Created a meeting notes doc in the Escort folder")
        return entry

    def _with_doc(self, meeting_id: str, meeting_title: str, write: Callable[[str], None]) -> dict[str, Any]:
        """Run a write against the meeting doc; if the user deleted it, start a fresh one once."""
        self.require_write()
        entry = self.ensure_meeting_doc(meeting_id, meeting_title)
        try:
            write(entry["doc_id"])
        except ConnectorError as err:
            if err.code != "not_found":
                raise
            docs = self.state.docs()
            docs.pop(meeting_id, None)
            self.state.set_docs(docs)
            entry = self.ensure_meeting_doc(meeting_id, meeting_title)
            write(entry["doc_id"])
        return entry

    def note_commitment(self, meeting_id: str, meeting_title: str, who: str, what: str, due: str | None) -> dict[str, Any]:
        line = commitment_line(who, what, due)
        return self._with_doc(meeting_id, meeting_title, lambda doc_id: self.append_text(doc_id, line))

    def save_summary(self, meeting_id: str, meeting_title: str, summary: dict[str, Any], stamp: str | None = None) -> dict[str, Any]:
        blocks = summary_blocks(summary, stamp or datetime.now().strftime("%Y-%m-%d %H:%M"))
        return self._with_doc(meeting_id, meeting_title, lambda doc_id: self.append_structured(doc_id, blocks))


def public_doc(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {"id": entry.get("doc_id"), "url": entry.get("url"), "title": entry.get("title"), "created_at": entry.get("created_at")}
