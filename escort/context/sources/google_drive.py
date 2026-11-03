"""Import Google Drive files via the Drive REST API (drive.readonly).

Binary files (PDF, DOCX, TXT, MD) download with files.get?alt=media; Google-native Docs,
Sheets and Slides export to text first.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from escort.context.chunk import sections_from_markdown
from escort.context.extract import extract_text
from escort.context.models import ContextItem
from escort.context.sources.base import ConnectorError, ConnectorScopeError, ConnectorSetupError
from escort.context.sources.google_auth import PROVIDER, GoogleAuth, consent_error
from escort.context.sources.google_source import MAX_DOC_CHARS, MAX_SHEET_COLS, MAX_SHEET_ROWS, table_markdown
from escort.store import new_id

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
SOURCE_KIND = "google_drive"
SOURCE_LABEL = "Google Drive"
MAX_BYTES = 15 * 1024 * 1024
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
# Google-native type -> (export mime types to try in order, extension the extractor understands)
NATIVE_EXPORTS = {
    GOOGLE_DOC: (("text/markdown", "text/plain"), ".md"),
    GOOGLE_SHEET: (("text/csv",), ".csv"),
    GOOGLE_SLIDES: (("text/plain",), ".txt"),
}
BINARY_TYPES = {
    "application/pdf": ".pdf",
    DOCX_MIME: ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
}
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".md", ".txt")
IMPORTABLE_MIMES = (
    GOOGLE_DOC,
    GOOGLE_SHEET,
    GOOGLE_SLIDES,
    "application/pdf",
    DOCX_MIME,
    "text/plain",
    "text/markdown",
    "text/x-markdown",
)
MIME_LABELS = {
    GOOGLE_DOC: "Google Doc",
    GOOGLE_SHEET: "Google Sheet",
    GOOGLE_SLIDES: "Google Slides",
    "application/pdf": "PDF",
    DOCX_MIME: "Word document",
    "text/plain": "Text file",
    "text/markdown": "Markdown",
    "text/x-markdown": "Markdown",
}
UNSUPPORTED = "Escort reads PDF, Word (.docx), text and Markdown files, and Google Docs, Sheets and Slides. That file is a different type."
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
SEARCH_PAGE_SIZE = 30


def _too_large(size: int) -> ConnectorSetupError:
    mb = MAX_BYTES // (1024 * 1024)
    return ConnectorSetupError(
        PROVIDER,
        f"That file is {size / (1024 * 1024):.1f} MB. Escort imports files up to {mb} MB; pick a smaller one or upload an excerpt.",
        code="too_large",
    )


def _extension(mime: str, name: str) -> str | None:
    if mime in BINARY_TYPES:
        return BINARY_TYPES[mime]
    lowered = name.lower()
    return next((ext for ext in SUPPORTED_EXTENSIONS if lowered.endswith(ext)), None)


def csv_to_markdown(text: str) -> str:
    rows = list(csv.reader(io.StringIO(text)))
    table, truncated = table_markdown(rows)
    lines = table or ["(This sheet is empty.)"]
    if truncated:
        lines += ["", f"_Showing the first {MAX_SHEET_ROWS} rows and {MAX_SHEET_COLS} columns; the sheet has more._"]
    return "\n".join(lines)


def _drive_query(text: str) -> str:
    mime_filter = " or ".join(f"mimeType='{mime}'" for mime in IMPORTABLE_MIMES)
    parts = [f"trashed=false and ({mime_filter})"]
    cleaned = (text or "").strip()
    if cleaned:
        escaped = cleaned.replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"name contains '{escaped}'")
    return " and ".join(parts)


def search_drive_files(auth: GoogleAuth, query: str, limit: int = 20) -> list[dict[str, Any]]:
    auth.require("drive_browse")
    capped = max(1, min(limit, 50))
    hits: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(hits) < capped:
        params: dict[str, str] = {
            "q": _drive_query(query),
            "pageSize": str(min(SEARCH_PAGE_SIZE, capped - len(hits))),
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,description)",
            "orderBy": "modifiedTime desc",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _call(auth, DRIVE_FILES_URL, params).json()
        for raw in payload.get("files") or []:
            if not isinstance(raw, dict):
                continue
            file_id = str(raw.get("id") or "")
            title = str(raw.get("name") or "Untitled")
            mime = str(raw.get("mimeType") or "")
            snippet = str(raw.get("description") or "").strip() or MIME_LABELS.get(mime, mime)
            hits.append(
                {
                    "id": file_id,
                    "title": title,
                    "snippet": snippet,
                    "mimeType": mime,
                    "modified": str(raw.get("modifiedTime") or ""),
                }
            )
            if len(hits) >= capped:
                break
        page_token = str(payload.get("nextPageToken") or "") or None
        if not page_token:
            break
    return hits[:capped]


def _call(auth: GoogleAuth, url: str, params: dict[str, str]) -> Any:
    try:
        return auth.request("GET", url, params=params)
    except ConnectorScopeError as err:
        raise consent_error("drive_browse") from err
    except ConnectorError as err:
        if err.code == "not_found":
            raise ConnectorError(
                PROVIDER,
                "Google could not find that file, or it is not shared with the Google account Escort is connected to.",
                code="not_found",
            ) from err
        if err.code == "permission_denied":
            raise ConnectorError(
                PROVIDER,
                "Google did not let Escort open that file. It may not be shared with the connected account, "
                "or its owner turned off downloading.",
                code="not_shared",
            ) from err
        raise


def _export(auth: GoogleAuth, file_id: str, mimes: tuple[str, ...]) -> bytes:
    last: ConnectorError | None = None
    for mime in mimes:
        try:
            return _call(auth, f"{DRIVE_FILES_URL}/{file_id}/export", {"mimeType": mime}).content
        except ConnectorError as err:
            # An export format Google does not offer for this file: try the next one.
            if err.code is not None or not str(err).startswith("Google API error 400"):
                raise
            last = err
    assert last is not None
    raise last


def fetch_drive_file(auth: GoogleAuth, file_id: str, mime_hint: str = "", name_hint: str = "") -> ContextItem:
    file_id = (file_id or "").strip()
    if not _FILE_ID.match(file_id):
        raise ConnectorSetupError(PROVIDER, "That is not a Google Drive file id.", code="bad_request")
    auth.require("drive_browse")
    meta = _call(
        auth,
        f"{DRIVE_FILES_URL}/{file_id}",
        {"fields": "id,name,mimeType,size", "supportsAllDrives": "true"},
    ).json()
    # Google's own metadata decides the type and size, not what the browser sent.
    mime = str(meta.get("mimeType") or mime_hint or "")
    name = str(meta.get("name") or name_hint or "Drive file")
    if mime in NATIVE_EXPORTS:
        mimes, ext = NATIVE_EXPORTS[mime]
        data = _export(auth, file_id, mimes)
        if len(data) > MAX_BYTES:
            raise _too_large(len(data))
        text = data.decode("utf-8", errors="replace")
        if mime == GOOGLE_SHEET:
            text = csv_to_markdown(text)
        filename = name if name.lower().endswith(ext) else f"{name}{ext}"
    else:
        ext = _extension(mime, name)
        if ext is None:
            raise ConnectorSetupError(PROVIDER, UNSUPPORTED, code="unsupported_file")
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > MAX_BYTES:
            raise _too_large(size)
        data = _call(auth, f"{DRIVE_FILES_URL}/{file_id}", {"alt": "media", "supportsAllDrives": "true"}).content
        if len(data) > MAX_BYTES:
            raise _too_large(len(data))
        filename = name if name.lower().endswith(ext) else f"{name}{ext}"
        try:
            text = extract_text(filename, data)
        except ValueError as err:
            raise ConnectorSetupError(PROVIDER, f"Escort could not read that file: {err}", code="unreadable") from err
        except Exception as err:  # a corrupt DOCX/PDF: say so instead of a 500
            raise ConnectorSetupError(PROVIDER, "Escort could not read that file; it may be damaged.", code="unreadable") from err
    if not text.strip():
        raise ConnectorSetupError(
            PROVIDER,
            "That file has no text Escort can read (a scanned PDF or an image-only file).",
            code="no_text",
        )
    return ContextItem(
        id=new_id("ctx"),
        source_kind=SOURCE_KIND,
        source_label=SOURCE_LABEL,
        title=name,
        sections=sections_from_markdown(text[:MAX_DOC_CHARS]),
        enabled=True,
        external_id=str(meta.get("id") or file_id),
    )
