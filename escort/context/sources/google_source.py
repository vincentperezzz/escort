"""Live Google Calendar + Drive clients built on GoogleAuth."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from escort.context.chunk import sections_from_markdown
from escort.context.extract import extract_text
from escort.context.models import CalendarEvent, ContextItem
from escort.context.sources.base import ConnectorAuthError, ConnectorError
from escort.context.sources.google_auth import PROVIDER, GoogleAuth
from escort.store import new_id

CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("text/markdown", "text/plain"),
    "application/vnd.google-apps.presentation": ("text/plain",),
    "application/vnd.google-apps.spreadsheet": ("text/csv",),
}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
KIND_LABELS = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/pdf": "PDF",
    DOCX_MIME: "Word document",
    "text/plain": "Text",
    "text/markdown": "Markdown",
}


def _drive_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


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


def search_drive(auth: GoogleAuth, query: str, limit: int = 8) -> list[dict[str, Any]]:
    params = {
        "pageSize": str(max(1, min(limit, 50))),
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "spaces": "drive",
    }
    text = query.strip()
    if text:
        literal = _drive_literal(text)
        params["q"] = f"trashed = false and (name contains '{literal}' or fullText contains '{literal}')"
    else:
        params["q"] = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        params["orderBy"] = "modifiedTime desc"
    response = auth.request("GET", DRIVE_FILES_URL, params=params)
    hits = []
    for raw in response.json().get("files") or []:
        mime = str(raw.get("mimeType") or "")
        if mime == "application/vnd.google-apps.folder":
            continue
        kind = KIND_LABELS.get(mime, mime.split("/")[-1] or "file")
        modified = str(raw.get("modifiedTime") or "")[:10]
        hits.append(
            {
                "id": str(raw.get("id")),
                "title": str(raw.get("name") or "Untitled"),
                "snippet": f"{kind} · modified {modified}" if modified else kind,
                "url": raw.get("webViewLink"),
                "mock": False,
            }
        )
    return hits[:limit]


def fetch_drive_item(auth: GoogleAuth, file_id: str) -> ContextItem:
    meta = auth.request(
        "GET",
        f"{DRIVE_FILES_URL}/{file_id}",
        params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"},
    ).json()
    name = str(meta.get("name") or file_id)
    mime = str(meta.get("mimeType") or "")
    text = _drive_text(auth, file_id, name, mime, meta.get("size"))
    title = name.rsplit(".", 1)[0] if "." in name and not mime.startswith("application/vnd.google-apps") else name
    body = text.strip()
    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"
    return ContextItem(
        id=new_id("ctx"),
        source_kind="drive",
        source_label="Drive",
        title=title,
        sections=sections_from_markdown(body),
        enabled=True,
        external_id=file_id,
    )


def _drive_text(auth: GoogleAuth, file_id: str, name: str, mime: str, size: Any) -> str:
    if mime in GOOGLE_EXPORTS:
        last: ConnectorError | None = None
        for export_mime in GOOGLE_EXPORTS[mime]:
            try:
                response = auth.request("GET", f"{DRIVE_FILES_URL}/{file_id}/export", params={"mimeType": export_mime})
                return response.text
            except ConnectorAuthError:
                raise
            except ConnectorError as err:
                last = err
        raise last or ConnectorError(PROVIDER, f"Could not export {name}")
    if size is not None and int(size) > MAX_DOWNLOAD_BYTES:
        raise ConnectorError(PROVIDER, f"{name} is too large to import")
    if mime == "application/pdf":
        filename = name if name.lower().endswith(".pdf") else f"{name}.pdf"
    elif mime == DOCX_MIME:
        filename = name if name.lower().endswith(".docx") else f"{name}.docx"
    elif mime.startswith("text/") or mime in {"application/json"}:
        filename = f"{name}.txt"
    else:
        raise ConnectorError(PROVIDER, f"Escort cannot read {KIND_LABELS.get(mime, mime) or 'this file type'} files yet")
    response = auth.request("GET", f"{DRIVE_FILES_URL}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"})
    try:
        return extract_text(filename, response.content)
    except ValueError as err:
        raise ConnectorError(PROVIDER, str(err)) from err
