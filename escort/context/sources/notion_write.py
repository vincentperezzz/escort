"""Save meeting notes to Notion: pages Escort created, plus existing pages the user picked.

Runs over Notion MCP when the user connected with Notion's one-click OAuth (McpPages), else over the
REST API with the pasted internal-integration token (RestPages). With a token, writing needs the integration's
'Insert content' and 'Update content' capabilities and the page shared with it; a
403/restricted_resource surfaces as a clear ConnectorScopeError (code needs_capabilities).

Layout: one child page per meeting, "Escort · <meeting title> · <YYYY-MM-DD>", under the
chosen parent. Existing pages can be kept up to date too, but only pages the user explicitly
picked as a notes target ("Choose a page to keep updated"); the guard refuses every other id
before any request. Updates read the page's current blocks first so edits are targeted: append
blocks, add an item under a heading, or replace one block's text (PATCH /v1/blocks/{id}).
Writes through the UI or chat are previewed and confirmed by the user first (see runtime).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from escort.context.sources.base import ConnectorAuthError, ConnectorError, ConnectorSetupError
from escort.context.sources.google_docs import commitment_line, doc_title
from escort.context.sources.notion_mcp import blocks_markdown, markdown_blocks, replace_prefix, unique_span
from escort.context.sources.notion_source import PROVIDER, NotionClient, _plain, block_line
from escort.store import utc_now

log = logging.getLogger("escort.notion.write")

MAX_TEXT = 1900  # Notion caps one rich_text item at 2000 characters
MAX_CHILDREN = 100  # and one append call at 100 blocks
MAX_READ_BLOCKS = 400
EDITABLE_KINDS = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
    "toggle",
}
HEADING_LEVEL = {"heading_1": 1, "heading_2": 2, "heading_3": 3}


class UnknownPageError(ConnectorError):
    """A write targeted a Notion page Escort did not create; refused without calling Notion."""

    status_code = 403
    code = "unknown_doc"


class HeadingNotFoundError(ConnectorError):
    status_code = 404
    code = "heading_not_found"


class BlockNotFoundError(ConnectorError):
    status_code = 404
    code = "block_not_found"


def _norm_id(value: str) -> str:
    return str(value or "").replace("-", "").lower()


@dataclass
class NotionNotesState:
    """Prefs-backed: the chosen parent page and the meeting_id -> page registry."""

    data: dict[str, Any]
    on_change: Callable[[], None] = lambda: None

    def parent(self) -> dict[str, str] | None:
        parent = self.data.get("notion_notes_parent")
        return dict(parent) if isinstance(parent, dict) and parent.get("id") else None

    def set_parent(self, page_id: str | None, title: str | None = None) -> None:
        if page_id:
            self.data["notion_notes_parent"] = {"id": str(page_id), "title": str(title or "Untitled")}
        else:
            self.data.pop("notion_notes_parent", None)
        self.on_change()

    def targets(self) -> dict[str, dict[str, Any]]:
        """Existing pages the user picked to keep updated: normalized id -> {id, title, picked_at}."""
        targets = self.data.get("notion_notes_targets")
        return dict(targets) if isinstance(targets, dict) else {}

    def add_target(self, page_id: str, title: str | None = None) -> dict[str, Any]:
        targets = self.targets()
        entry = {"id": str(page_id), "title": str(title or "Untitled"), "picked_at": utc_now()}
        targets[_norm_id(page_id)] = entry
        self.data["notion_notes_targets"] = targets
        self.on_change()
        return entry

    def remove_target(self, page_id: str) -> bool:
        targets = self.targets()
        removed = targets.pop(_norm_id(page_id), None) is not None
        self.data["notion_notes_targets"] = targets
        self.on_change()
        return removed

    def pages(self) -> dict[str, dict[str, Any]]:
        pages = self.data.get("notion_notes_pages")
        return dict(pages) if isinstance(pages, dict) else {}

    def set_pages(self, pages: dict[str, dict[str, Any]]) -> None:
        self.data["notion_notes_pages"] = pages
        self.on_change()


def _text(content: str) -> list[dict[str, Any]]:
    content = content if len(content) <= MAX_TEXT else content[: MAX_TEXT - 1] + "…"
    return [{"type": "text", "text": {"content": content}}]


def block(kind: str, content: str) -> dict[str, Any]:
    return {"object": "block", "type": kind, kind: {"rich_text": _text(content)}}


def item_block(text: str, checkbox: bool = False) -> dict[str, Any]:
    if checkbox:
        return {"object": "block", "type": "to_do", "to_do": {"rich_text": _text(text), "checked": False}}
    return block("bulleted_list_item", text)


def block_preview_text(item: dict[str, Any]) -> str:
    """Readable text of a block we are about to send, for the confirmation preview."""
    kind = item.get("type") or ""
    text = "".join(str((part.get("text") or {}).get("content") or "") for part in (item.get(kind) or {}).get("rich_text") or [])
    if kind == "bulleted_list_item":
        return f"- {text}"
    if kind == "to_do":
        return f"[ ] {text}"
    if kind.startswith("heading_"):
        return text
    return text


def commitment_block(who: str, what: str, due: str | None) -> dict[str, Any]:
    return block("bulleted_list_item", commitment_line(who, what, due).removeprefix("- "))


def summary_children(summary: dict[str, Any], stamp: str) -> list[dict[str, Any]]:
    """One dated recap section: heading, then Summary / Commitments / Conflicts."""
    out = [block("heading_2", f"Recap · {stamp}"), block("heading_3", "Summary")]
    bullets = [str(item).strip() for item in summary.get("bullets") or [] if str(item).strip()]
    out += [block("bulleted_list_item", item) for item in bullets] or [block("paragraph", "Nothing notable.")]
    out.append(block("heading_3", "Commitments"))
    commitments = [item for item in summary.get("commitments") or [] if isinstance(item, dict) and item.get("what")]
    out += [commitment_block(str(c.get("who") or ""), str(c["what"]), str(c.get("due") or "")) for c in commitments] or [
        block("paragraph", "No commitments captured.")
    ]
    out.append(block("heading_3", "Conflicts"))
    conflicts = [item for item in summary.get("conflicts") or [] if isinstance(item, dict) and item.get("title")]
    out += [
        block("bulleted_list_item", str(c["title"]) + (f" ({c['section']})" if c.get("section") else "")) for c in conflicts
    ] or [block("paragraph", "No conflicts against your notes.")]
    return out


class RestPages:
    """Page operations over the REST API with the pasted integration token."""

    def __init__(self, client: NotionClient) -> None:
        self.client = client

    def create_page(self, parent_id: str, title: str) -> dict[str, Any]:
        page = self.client._call(
            "POST",
            "/pages",
            json={"parent": {"page_id": parent_id}, "properties": {"title": {"title": _text(title)}}},
        )
        return {"id": str(page["id"]), "url": str(page.get("url") or "")}

    def append(self, page_id: str, children: list[dict[str, Any]], after: str | None = None) -> None:
        for start in range(0, len(children), MAX_CHILDREN):
            body: dict[str, Any] = {"children": children[start : start + MAX_CHILDREN]}
            if after:
                body["after"] = after  # only the first batch is anchored; later ones follow it
            self.client._call("PATCH", f"/blocks/{page_id}/children", json=body)
            after = None

    def read_blocks(self, page_id: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(blocks) < MAX_READ_BLOCKS:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self.client._call("GET", f"/blocks/{page_id}/children", params=params)
            for raw in payload.get("results") or []:
                kind = str(raw.get("type") or "")
                blocks.append(
                    {
                        "id": str(raw.get("id")),
                        "type": kind,
                        "text": _plain((raw.get(kind) or {}).get("rich_text")),
                        "line": block_line(raw) or "",
                        "level": HEADING_LEVEL.get(kind, 0),
                    }
                )
            cursor = payload.get("next_cursor")
            if not payload.get("has_more") or not cursor:
                break
        return blocks[:MAX_READ_BLOCKS]

    def edit(self, page_id: str, target: dict[str, Any], text: str) -> None:
        kind = target["type"]
        self.client._call("PATCH", f"/blocks/{target['id']}", json={kind: {"rich_text": _text(text)}})


class McpPages:
    """The same page operations through Notion MCP tools (OAuth connection).

    Notion MCP edits markdown, not block ids, so a page is read as lines and each line gets a
    content-hashed id; edits name the exact text that was on the page when it was read.
    """

    def __init__(self, mcp: Any) -> None:
        self.mcp = mcp

    def create_page(self, parent_id: str, title: str) -> dict[str, Any]:
        return self.mcp.create_page(parent_id, title)

    def _lines(self, page_id: str) -> tuple[list[str], list[dict[str, Any]]]:
        _title, body = self.mcp.fetch_markdown(page_id)
        return markdown_blocks(body)

    def append(self, page_id: str, children: list[dict[str, Any]], after: str | None = None) -> None:
        markdown = blocks_markdown(children)
        lines, blocks = self._lines(page_id)
        if after:
            anchor = next((item for item in blocks if item["id"] == after), None)
            if anchor is None:
                raise BlockNotFoundError(PROVIDER, "That part of the page changed. Open the page and try again.")
            index = anchor["index"]
        else:
            index = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
        if index is None:
            self.mcp.write_empty(page_id, markdown)
            return
        _start, span = unique_span(lines, index)
        self.mcp.insert_after(page_id, span, markdown)

    def read_blocks(self, page_id: str) -> list[dict[str, Any]]:
        return self._lines(page_id)[1][:MAX_READ_BLOCKS]

    def edit(self, page_id: str, target: dict[str, Any], text: str) -> None:
        lines, blocks = self._lines(page_id)
        current = next((item for item in blocks if item["id"] == target["id"]), None)
        if current is None:
            raise BlockNotFoundError(PROVIDER, "That block is not on the page anymore. Open the page and try again.")
        index = current["index"]
        start, span = unique_span(lines, index)
        new = "\n".join([*lines[start:index], replace_prefix(lines[index], text)])
        self.mcp.replace_span(page_id, span, new)


class NotionNotes:
    def __init__(self, client: NotionClient, state: NotionNotesState, mcp: Any = None) -> None:
        self.client = client
        self.state = state
        self.mcp = mcp

    def backend(self) -> RestPages | McpPages:
        """MCP when connected through Notion's OAuth; the pasted-token REST API otherwise."""
        if self.mcp is not None and self.mcp.connected():
            return McpPages(self.mcp)
        return RestPages(self.client)

    def connected(self) -> bool:
        return self.client.connected() or (self.mcp is not None and self.mcp.connected())

    def require_write(self) -> dict[str, str]:
        if not self.connected():
            raise ConnectorAuthError(PROVIDER, "Connect Notion first, then turn on Save notes to Notion.", code="not_connected")
        parent = self.state.parent()
        if parent is None:
            raise ConnectorSetupError(PROVIDER, "Pick the Notion page Escort should save meeting pages under.", code="no_parent")
        return parent

    def _allowed(self, page_id: str) -> None:
        wanted = _norm_id(page_id)
        parent = self.state.parent()
        if parent and _norm_id(parent["id"]) == wanted:
            return
        if any(_norm_id(entry.get("page_id", "")) == wanted for entry in self.state.pages().values()):
            return
        if wanted in self.state.targets():
            return
        raise UnknownPageError(
            PROVIDER,
            "Escort only writes to Notion pages it created or that you picked under Choose a page to keep updated.",
        )

    def create_page(self, title: str) -> dict[str, Any]:
        parent = self.require_write()
        page = self.backend().create_page(parent["id"], title)
        return {"page_id": page["id"], "url": page["url"], "title": title, "created_at": utc_now()}

    def append_blocks(self, page_id: str, children: list[dict[str, Any]], after: str | None = None) -> None:
        self._allowed(page_id)
        self.require_write()
        self.backend().append(page_id, children, after=after)

    # ---- existing pages: read, then targeted edits -----------------------------------
    def read_page(self, page_id: str) -> list[dict[str, Any]]:
        """Top-level blocks of a page as [{id, type, text, line, level}], read with the same connection."""
        self._allowed(page_id)
        if not self.connected():
            raise ConnectorAuthError(PROVIDER, "Connect Notion first.", code="not_connected")
        return self.backend().read_blocks(page_id)

    def edit_block_text(self, page_id: str, block_id: str, text: str, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Replace one block's text (PATCH /v1/blocks/{id}); the block must be on the page, read just now."""
        self._allowed(page_id)
        self.require_write()
        current = blocks if blocks is not None else self.read_page(page_id)
        target = next((item for item in current if _norm_id(item["id"]) == _norm_id(block_id)), None)
        if target is None:
            raise BlockNotFoundError(PROVIDER, "That block is not on the page anymore. Open the page and try again.")
        if target["type"] not in EDITABLE_KINDS:
            raise ConnectorSetupError(PROVIDER, "Escort can only edit the text of paragraphs, headings, list items and to-dos.", code="not_editable")
        self.backend().edit(page_id, target, text)
        return {"block_id": target["id"], "before": target["text"], "after": text}

    @staticmethod
    def find_heading(blocks: list[dict[str, Any]], heading: str) -> int | None:
        wanted = " ".join(heading.lower().split())
        headings = [(i, " ".join(b["text"].lower().split())) for i, b in enumerate(blocks) if b["level"]]
        for i, text in headings:
            if text == wanted:
                return i
        for i, text in headings:
            if wanted and wanted in text:
                return i
        return None

    def item_placement(self, blocks: list[dict[str, Any]], heading: str) -> tuple[str | None, bool]:
        """(block id to insert after, heading_exists). Insert at the end of that heading's section."""
        start = self.find_heading(blocks, heading)
        if start is None:
            return None, False
        level = blocks[start]["level"]
        last = start
        for index in range(start + 1, len(blocks)):
            if blocks[index]["level"] and blocks[index]["level"] <= level:
                break
            last = index
        return blocks[last]["id"], True

    def add_item_under_heading(
        self, page_id: str, heading: str, text: str, checkbox: bool = False, blocks: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Add a bulleted (or checkbox) item at the end of a heading's section; makes the heading if missing."""
        self._allowed(page_id)
        self.require_write()
        current = blocks if blocks is not None else self.read_page(page_id)
        after, exists = self.item_placement(current, heading)
        item = item_block(text, checkbox)
        if exists:
            self.append_blocks(page_id, [item], after=after)
        else:
            self.append_blocks(page_id, [block("heading_3", heading), item])
        return {"heading": heading, "heading_created": not exists}

    def page_for(self, meeting_id: str) -> dict[str, Any] | None:
        entry = self.state.pages().get(meeting_id)
        return dict(entry) if entry else None

    def ensure_meeting_page(self, meeting_id: str, meeting_title: str) -> dict[str, Any]:
        existing = self.page_for(meeting_id)
        if existing:
            return existing
        entry = {**self.create_page(doc_title(meeting_title)), "meeting_id": meeting_id}
        pages = self.state.pages()
        pages[meeting_id] = entry
        self.state.set_pages(pages)
        log.info("Created a meeting page under the chosen Notion parent")
        return entry

    def _with_page(self, meeting_id: str, meeting_title: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        self.require_write()
        entry = self.ensure_meeting_page(meeting_id, meeting_title)
        try:
            self.append_blocks(entry["page_id"], children)
        except ConnectorError as err:
            if err.code != "not_found":
                raise
            pages = self.state.pages()  # the user deleted the page: start a fresh one once
            pages.pop(meeting_id, None)
            self.state.set_pages(pages)
            entry = self.ensure_meeting_page(meeting_id, meeting_title)
            self.append_blocks(entry["page_id"], children)
        return entry

    def note_commitment(self, meeting_id: str, meeting_title: str, who: str, what: str, due: str | None) -> dict[str, Any]:
        return self._with_page(meeting_id, meeting_title, [commitment_block(who, what, due)])

    def save_summary(self, meeting_id: str, meeting_title: str, summary: dict[str, Any], stamp: str | None = None) -> dict[str, Any]:
        children = summary_children(summary, stamp or datetime.now().strftime("%Y-%m-%d %H:%M"))
        return self._with_page(meeting_id, meeting_title, children)


class UpdatePlans:
    """Builds the exact change for an existing page (reading it first) and applies it after the user confirms."""

    COMMITMENT_HEADING = "Commitments"

    def __init__(self, notes: NotionNotes) -> None:
        self.notes = notes

    def _target(self, page_id: str) -> dict[str, Any]:
        self.notes._allowed(page_id)
        self.notes.require_write()
        for entry in [*self.notes.state.targets().values(), *self.notes.state.pages().values()]:
            if _norm_id(entry.get("id") or entry.get("page_id") or "") == _norm_id(page_id):
                return {"id": str(entry.get("id") or entry.get("page_id")), "title": str(entry.get("title") or "Untitled")}
        return {"id": page_id, "title": "this page"}

    def commitment(self, page_id: str, who: str, what: str, due: str | None, checkbox: bool = False) -> dict[str, Any]:
        page = self._target(page_id)
        blocks = self.notes.read_page(page_id)
        line = commitment_line(who, what, due)
        text = line.removeprefix("- ")
        after, exists = self.notes.item_placement(blocks, self.COMMITMENT_HEADING)
        lines = [line] if exists else [self.COMMITMENT_HEADING, line]
        where = f"under {self.COMMITMENT_HEADING}" if exists else f"at the end (new {self.COMMITMENT_HEADING} heading)"
        return {
            "page": page,
            "action": "add_item",
            "summary": f"Add to {page['title']} {where}:",
            "lines": lines,
            "op": {"type": "add_item", "heading": self.COMMITMENT_HEADING, "text": text, "checkbox": checkbox},
        }

    def summary(self, page_id: str, summary: dict[str, Any], stamp: str | None = None) -> dict[str, Any]:
        page = self._target(page_id)
        children = summary_children(summary, stamp or datetime.now().strftime("%Y-%m-%d %H:%M"))
        return {
            "page": page,
            "action": "append",
            "summary": f"Add a dated recap to the end of {page['title']}:",
            "lines": [block_preview_text(item) for item in children],
            "op": {"type": "append", "children": children},
        }

    def edit(self, page_id: str, block_id: str, text: str) -> dict[str, Any]:
        page = self._target(page_id)
        blocks = self.notes.read_page(page_id)
        target = next((item for item in blocks if _norm_id(item["id"]) == _norm_id(block_id)), None)
        if target is None:
            raise BlockNotFoundError(PROVIDER, "That block is not on the page anymore.")
        return {
            "page": page,
            "action": "edit",
            "summary": f"Change one line on {page['title']}:",
            "lines": [f"Before: {target['line'] or target['text']}", f"After: {text}"],
            "op": {"type": "edit", "block_id": target["id"], "text": text},
        }

    def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        page_id = plan["page"]["id"]
        op = plan["op"]
        if op["type"] == "append":
            self.notes.append_blocks(page_id, op["children"])
        elif op["type"] == "add_item":
            self.notes.add_item_under_heading(page_id, op["heading"], op["text"], checkbox=bool(op.get("checkbox")))
        elif op["type"] == "edit":
            self.notes.edit_block_text(page_id, op["block_id"], op["text"])
        else:
            raise ConnectorSetupError(PROVIDER, "Unknown update.", code="bad_request")
        return plan["page"]


def public_target(entry: dict[str, Any]) -> dict[str, Any]:
    return {"id": entry.get("id"), "title": entry.get("title"), "picked_at": entry.get("picked_at")}


def public_page(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {"id": entry.get("page_id"), "url": entry.get("url"), "title": entry.get("title"), "created_at": entry.get("created_at")}
