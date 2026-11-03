"""Live Google Calendar client and the pasted-link importer (Docs, Sheets, Slides), built on GoogleAuth.

No Drive-wide access: a Doc, Sheet or Slides deck is read only when the user pastes its link.
Docs uses documents.readonly (base sign-in); Sheets and Slides ask for their own read-only
scope the first time a link of that kind is pasted.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from escort.context.chunk import sections_from_markdown
from escort.context.models import CalendarEvent, ContextItem
from escort.context.sources.base import ConnectorError, ConnectorSetupError
from escort.context.sources.google_auth import PROVIDER, GoogleAuth
from escort.store import new_id

CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
DOCS_URL = "https://docs.googleapis.com/v1/documents"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
SLIDES_URL = "https://slides.googleapis.com/v1/presentations"
DOC_FIELDS = "documentId,title,body.content,lists"
SHEET_FIELDS = "spreadsheetId,properties.title,sheets.properties(sheetId,title,sheetType,gridProperties)"
SLIDE_FIELDS = "presentationId,title,slides(objectId,pageElements,slideProperties(notesPage(notesProperties,pageElements)))"
SOURCE_KIND = "google_docs"
SOURCE_LABEL = "Google Docs"
# kind -> (context item source_kind, label, noun used in messages)
KINDS = {
    "docs": ("google_docs", "Google Docs", "document"),
    "sheets": ("google_sheets", "Google Sheets", "spreadsheet"),
    "slides": ("google_slides", "Google Slides", "presentation"),
}
LINK_KINDS = {"document": "docs", "spreadsheets": "sheets", "presentation": "slides"}
MAX_DOC_CHARS = 400_000
MAX_SHEET_ROWS = 200
MAX_SHEET_COLS = 30
MAX_SHEET_TABS = 20
UNSUPPORTED = (
    "Escort reads Google Docs, Sheets and Slides links. "
    "For other Drive files (PDF, Word, text), use Import from Google and search your Drive."
)
BAD_LINK = (
    "That does not look like a Google Docs, Sheets or Slides link. "
    "Paste a docs.google.com/... link, or an id with its type selected."
)

_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_LINK_PATH = re.compile(r"/(document|spreadsheets|presentation)/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")
_OTHER_KINDS = ("forms", "drawings")


def _link_error(message: str, code: str) -> ConnectorSetupError:
    return ConnectorSetupError(PROVIDER, message, code=code)


def parse_google_link(text: str, default_kind: str = "docs") -> tuple[str, str]:
    """(kind, id) from a pasted Docs/Sheets/Slides link; a bare id takes `default_kind`."""
    raw = (text or "").strip().strip("<>\"' ")
    if not raw:
        raise _link_error("Paste a Google Docs, Sheets or Slides link first.", "bad_link")
    if _BARE_ID.match(raw):
        if default_kind not in KINDS:
            raise _link_error(BAD_LINK, "bad_link")
        return default_kind, raw
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        host = (urllib.parse.urlsplit(candidate).hostname or "").lower()
        path = urllib.parse.urlsplit(candidate).path
    except ValueError:
        raise _link_error(BAD_LINK, "bad_link") from None
    if host == "drive.google.com" or host.endswith(".drive.google.com"):
        raise _link_error(UNSUPPORTED, "unsupported_link")
    if host != "docs.google.com":
        raise _link_error(BAD_LINK, "bad_link")
    if any(f"/{kind}/" in f"{path}/" for kind in _OTHER_KINDS):
        raise _link_error(UNSUPPORTED, "unsupported_link")
    if re.search(r"/(document|spreadsheets|presentation)/d/e/", path):
        raise _link_error(
            "That is a published-to-the-web link, which Google does not let Escort read. "
            "Open the file and copy the link from your browser's address bar.",
            "unsupported_link",
        )
    match = _LINK_PATH.search(path)
    if not match:
        raise _link_error(BAD_LINK, "bad_link")
    return LINK_KINDS[match.group(1)], match.group(2)


def parse_doc_link(text: str) -> str:
    """The Google Docs document id from a pasted link or a bare id; other kinds are refused."""
    kind, doc_id = parse_google_link(text, "docs")
    if kind != "docs":
        raise _link_error(f"That is a {KINDS[kind][1]} link, not a Google Docs link.", "wrong_kind")
    return doc_id


def list_calendar_events(auth: GoogleAuth, limit: int = 5, now: datetime | None = None) -> list[CalendarEvent]:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    response = auth.request(
        "GET",
        CALENDAR_EVENTS_URL,
        params={
            "timeMin": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(max(1, min(limit, 50))),
        },
    )
    events: list[CalendarEvent] = []
    for raw in response.json().get("items") or []:
        if raw.get("status") == "cancelled":
            continue
        start = raw.get("start") or {}
        end = raw.get("end") or {}
        hints = [str(item.get("title")) for item in raw.get("attachments") or [] if item.get("title")]
        events.append(
            CalendarEvent(
                id=str(raw.get("id") or ""),
                title=str(raw.get("summary") or "(no title)"),
                start=str(start.get("dateTime") or start.get("date") or ""),
                end=str(end.get("dateTime") or end.get("date") or ""),
                description=str(raw.get("description") or ""),
                attendees=[str(item.get("email")) for item in raw.get("attendees") or [] if item.get("email")],
                linked_doc_hints=hints,
            )
        )
    return events[:limit]


# ---- Google Docs -> markdown (the same shape the Notion importer produces) ---------------------


def _run_text(element: dict[str, Any]) -> str:
    run = element.get("textRun")
    if run:
        return str(run.get("content") or "")
    person = (element.get("person") or {}).get("personProperties") or {}
    if person:
        return str(person.get("name") or person.get("email") or "")
    rich = (element.get("richLink") or {}).get("richLinkProperties") or {}
    if rich:
        return str(rich.get("title") or rich.get("uri") or "")
    return ""


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    text = "".join(_run_text(el) for el in paragraph.get("elements") or [])
    return " ".join(text.replace("\u000b", " ").split())


def _is_ordered(lists: dict[str, Any], paragraph: dict[str, Any]) -> bool:
    bullet = paragraph.get("bullet") or {}
    levels = ((lists.get(bullet.get("listId")) or {}).get("listProperties") or {}).get("nestingLevels") or []
    level = int(bullet.get("nestingLevel") or 0)
    if level < len(levels):
        glyph = str(levels[level].get("glyphType") or "")
        return bool(glyph) and glyph not in {"GLYPH_TYPE_UNSPECIFIED", "NONE"}
    return False


def _paragraph_line(lists: dict[str, Any], paragraph: dict[str, Any]) -> str:
    text = _paragraph_text(paragraph)
    if not text:
        return ""
    style = str((paragraph.get("paragraphStyle") or {}).get("namedStyleType") or "NORMAL_TEXT")
    if style == "TITLE":
        return f"# {text}"
    if style.startswith("HEADING_") and style.removeprefix("HEADING_").isdigit():
        return f"{'#' * min(int(style.removeprefix('HEADING_')), 6)} {text}"
    if paragraph.get("bullet"):
        indent = "  " * int(paragraph["bullet"].get("nestingLevel") or 0)
        return f"{indent}{'1.' if _is_ordered(lists, paragraph) else '-'} {text}"
    return text


def _table_lines(table: dict[str, Any]) -> list[str]:
    lines = []
    for row in table.get("tableRows") or []:
        cells = []
        for cell in row.get("tableCells") or []:
            parts = [
                _paragraph_text(el["paragraph"]) for el in cell.get("content") or [] if isinstance(el.get("paragraph"), dict)
            ]
            cells.append(" ".join(part for part in parts if part))
        if any(cells):
            lines.append(" | ".join(cells))
    return lines


def doc_to_markdown(doc: dict[str, Any]) -> str:
    """Headings, paragraphs, lists and tables of a documents.get response as markdown."""
    title = str(doc.get("title") or "Untitled document")
    lists = doc.get("lists") or {}
    lines: list[str] = [f"# {title}", ""]
    for element in (doc.get("body") or {}).get("content") or []:
        if isinstance(element.get("paragraph"), dict):
            lines.append(_paragraph_line(lists, element["paragraph"]))
        elif isinstance(element.get("table"), dict):
            lines.extend(_table_lines(element["table"]))
            lines.append("")
    return "\n".join(lines)[:MAX_DOC_CHARS]


def friendly_get(auth: GoogleAuth, url: str, noun: str, params: Any = None) -> Any:
    """GET a Google API resource; 404/403 become 'not found / not shared with this account'."""
    try:
        return auth.request("GET", url, params=params)
    except ConnectorError as err:
        if err.code == "not_found":
            raise ConnectorError(
                PROVIDER,
                f"Google could not find that {noun}, or it is not shared with the Google account Escort is connected to.",
                code="not_found",
            ) from err
        if err.code == "permission_denied":
            raise ConnectorError(
                PROVIDER,
                f"That {noun} is not shared with the Google account Escort is connected to. "
                "Ask for access, or connect the account that has it.",
                code="not_shared",
            ) from err
        raise


def _item(kind: str, title: str, markdown: str, external_id: str) -> ContextItem:
    source_kind, label, _noun = KINDS[kind]
    return ContextItem(
        id=new_id("ctx"),
        source_kind=source_kind,
        source_label=label,
        title=title,
        sections=sections_from_markdown(markdown[:MAX_DOC_CHARS]),
        enabled=True,
        external_id=external_id,
    )


def fetch_google_doc(auth: GoogleAuth, link: str) -> ContextItem:
    """Import the Google Doc a user pasted (link or id) as a context item labeled 'Google Docs'."""
    doc_id = parse_doc_link(link)
    auth.require("docs")
    doc = friendly_get(auth, f"{DOCS_URL}/{doc_id}", "document", {"fields": DOC_FIELDS}).json()
    title = str(doc.get("title") or "Untitled document")
    return _item("docs", title, doc_to_markdown(doc), str(doc.get("documentId") or doc_id))


def fetch_google_link(auth: GoogleAuth, link: str, default_kind: str = "docs") -> ContextItem:
    """Import whatever Docs/Sheets/Slides link the user pasted."""
    kind, file_id = parse_google_link(link, default_kind)
    if kind == "sheets":
        return fetch_google_sheet(auth, file_id)
    if kind == "slides":
        return fetch_google_slides(auth, file_id)
    return fetch_google_doc(auth, file_id)


# ---- Google Sheets -> one markdown table per tab ------------------------------------------------


def column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while index > 0:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _a1_range(tab: str, rows: int, cols: int) -> str:
    quoted = "'" + tab.replace("'", "''") + "'"
    return f"{quoted}!A1:{column_letter(cols)}{rows}"


def _cell(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).replace("|", "\\|")


def table_markdown(rows: list[list[Any]], max_rows: int = MAX_SHEET_ROWS, max_cols: int = MAX_SHEET_COLS) -> tuple[list[str], bool]:
    """A markdown table of the first max_rows x max_cols cells (first row as header), and whether it was cut."""
    # Trailing empty rows carry nothing; Sheets usually omits them already.
    while rows and not any(_cell(value) for value in rows[-1]):
        rows = rows[:-1]
    truncated = len(rows) > max_rows or any(len(row) > max_cols for row in rows)
    kept = [[_cell(value) for value in row[:max_cols]] for row in rows[:max_rows]]
    width = max((len(row) for row in kept), default=0)
    if not width:
        return [], truncated
    padded = [row + [""] * (width - len(row)) for row in kept]
    lines = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in padded[1:]]
    return lines, truncated


def sheet_to_markdown(meta: dict[str, Any], value_ranges: list[dict[str, Any]], tabs: list[str], skipped_tabs: int = 0) -> str:
    title = str((meta.get("properties") or {}).get("title") or "Untitled spreadsheet")
    lines = [f"# {title}", ""]
    for tab, block in zip(tabs, value_ranges, strict=False):
        lines += [f"## {tab}", ""]
        table, truncated = table_markdown(list(block.get("values") or []))
        lines += table or ["(This tab is empty.)"]
        if truncated:
            lines += ["", f"_Showing the first {MAX_SHEET_ROWS} rows and {MAX_SHEET_COLS} columns; this tab has more._"]
        lines.append("")
    if skipped_tabs:
        lines.append(f"_{skipped_tabs} more tab{'s' if skipped_tabs != 1 else ''} not imported (limit {MAX_SHEET_TABS})._")
    return "\n".join(lines)


def fetch_google_sheet(auth: GoogleAuth, sheet_id: str) -> ContextItem:
    """spreadsheets.get (no grid data) for the tab names, then one values.batchGet for all tabs."""
    auth.require("sheets")
    meta = friendly_get(auth, f"{SHEETS_URL}/{sheet_id}", "spreadsheet", {"includeGridData": "false", "fields": SHEET_FIELDS}).json()
    grid_tabs = [
        str((sheet.get("properties") or {}).get("title") or "")
        for sheet in meta.get("sheets") or []
        if (sheet.get("properties") or {}).get("sheetType", "GRID") == "GRID"
    ]
    grid_tabs = [tab for tab in grid_tabs if tab]
    tabs = grid_tabs[:MAX_SHEET_TABS]
    value_ranges: list[dict[str, Any]] = []
    if tabs:
        # One extra row and column so a cut-off sheet is detected exactly, not guessed from grid size.
        params = [("ranges", _a1_range(tab, MAX_SHEET_ROWS + 1, MAX_SHEET_COLS + 1)) for tab in tabs]
        params += [("majorDimension", "ROWS"), ("valueRenderOption", "FORMATTED_VALUE")]
        batch = friendly_get(auth, f"{SHEETS_URL}/{sheet_id}/values:batchGet", "spreadsheet", params).json()
        value_ranges = list(batch.get("valueRanges") or [])
    markdown = sheet_to_markdown(meta, value_ranges + [{}] * (len(tabs) - len(value_ranges)), tabs, len(grid_tabs) - len(tabs))
    title = str((meta.get("properties") or {}).get("title") or "Untitled spreadsheet")
    return _item("sheets", title, markdown, str(meta.get("spreadsheetId") or sheet_id))


# ---- Google Slides -> one section per slide -----------------------------------------------------

_TITLE_PLACEHOLDERS = {"TITLE", "CENTERED_TITLE"}


def _shape_text(shape: dict[str, Any]) -> str:
    parts = [str((el.get("textRun") or {}).get("content") or "") for el in (shape.get("text") or {}).get("textElements") or []]
    return "".join(parts).replace("\u000b", "\n")


def _safe_lines(text: str) -> list[str]:
    # A line starting with '#' would be read as a new section heading.
    return [("\\" + line if line.startswith("#") else line) for line in (raw.strip() for raw in text.splitlines()) if line]


def _walk_elements(elements: list[dict[str, Any]], title: list[str], body: list[str]) -> None:
    for element in elements or []:
        shape = element.get("shape")
        if isinstance(shape, dict):
            text = _shape_text(shape)
            placeholder = str((shape.get("placeholder") or {}).get("type") or "")
            if placeholder in _TITLE_PLACEHOLDERS and not title and text.strip():
                title.append(" ".join(text.split()))
            else:
                body.extend(_safe_lines(text))
        table = element.get("table")
        if isinstance(table, dict):
            for row in table.get("tableRows") or []:
                cells = [" ".join(_shape_text(cell).split()) for cell in row.get("tableCells") or []]
                if any(cells):
                    body.append(" | ".join(cells))
        group = element.get("elementGroup")
        if isinstance(group, dict):
            _walk_elements(group.get("children") or [], title, body)


def _speaker_notes(slide: dict[str, Any]) -> list[str]:
    notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
    notes_id = (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")
    for element in notes_page.get("pageElements") or []:
        if notes_id and element.get("objectId") == notes_id and isinstance(element.get("shape"), dict):
            return _safe_lines(_shape_text(element["shape"]))
    return []


def slides_to_markdown(deck: dict[str, Any]) -> str:
    lines = [f"# {deck.get('title') or 'Untitled presentation'}", ""]
    for number, slide in enumerate(deck.get("slides") or [], start=1):
        title: list[str] = []
        body: list[str] = []
        _walk_elements(slide.get("pageElements") or [], title, body)
        heading = f"Slide {number}: {title[0]}" if title else f"Slide {number}"
        lines += [f"## {heading}", ""]
        lines += body or ["(No text on this slide.)"]
        notes = _speaker_notes(slide)
        if notes:
            lines += ["", "Speaker notes:", *notes]
        lines.append("")
    return "\n".join(lines)


def fetch_google_slides(auth: GoogleAuth, deck_id: str) -> ContextItem:
    auth.require("slides")
    deck = friendly_get(auth, f"{SLIDES_URL}/{deck_id}", "presentation", {"fields": SLIDE_FIELDS}).json()
    title = str(deck.get("title") or "Untitled presentation")
    return _item("slides", title, slides_to_markdown(deck), str(deck.get("presentationId") or deck_id))
