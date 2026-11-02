"""Live Notion client using an internal-integration token pasted by the user.

Notion public OAuth needs a server-held client secret, so Escort does not do OAuth here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from escort.context.chunk import sections_from_markdown
from escort.context.models import ContextItem
from escort.context.sources.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorScopeError,
    ConnectorSetupError,
)
from escort.store import new_id

INTEGRATIONS_URL = "https://www.notion.so/profile/integrations"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TOKEN_KEY = "notion_oauth_token"
PROVIDER = "notion"
MAX_BLOCKS = 600
MAX_DEPTH = 3


def _plain(rich: list[dict[str, Any]] | None) -> str:
    return "".join(str(part.get("plain_text") or "") for part in rich or [])


def page_title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = _plain(prop.get("title"))
            if title:
                return title
    if page.get("title"):
        return _plain(page.get("title")) or "Untitled"
    return "Untitled"


def block_line(block: dict[str, Any]) -> str | None:
    kind = str(block.get("type") or "")
    data = block.get(kind) or {}
    text = _plain(data.get("rich_text"))
    if kind == "heading_1":
        return f"# {text}"
    if kind == "heading_2":
        return f"## {text}"
    if kind == "heading_3":
        return f"### {text}"
    if kind in {"paragraph", "callout", "toggle"}:
        return text
    if kind == "bulleted_list_item":
        return f"- {text}"
    if kind == "numbered_list_item":
        return f"1. {text}"
    if kind == "to_do":
        return f"- [{'x' if data.get('checked') else ' '}] {text}"
    if kind == "quote":
        return f"> {text}"
    if kind == "code":
        return f"```\n{text}\n```"
    if kind == "child_page":
        return f"## {data.get('title') or 'Subpage'}"
    if kind == "divider":
        return ""
    if kind == "table_row":
        return " | ".join(_plain(cell) for cell in data.get("cells") or [])
    return None


class NotionClient:
    def __init__(self, secrets: Any, http: httpx.Client) -> None:
        self.secrets = secrets
        self.http = http
        self.last_error: str | None = None
        self.on_change: Callable[[], None] = lambda: None

    # ---- storage -------------------------------------------------------
    def _load(self) -> dict[str, Any] | None:
        raw = self.secrets.get(TOKEN_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = None
        if not isinstance(data, dict):
            return {"token": str(raw), "account": None}
        return data if data.get("token") else None

    def token(self) -> str | None:
        data = self._load()
        return str(data["token"]) if data else None

    def connected(self) -> bool:
        return self._load() is not None

    def account(self) -> str | None:
        return (self._load() or {}).get("account") or None

    # ---- http ----------------------------------------------------------
    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _call(self, method: str, path: str, token: str | None = None, clear_on_401: bool = True, **kwargs: Any) -> dict[str, Any]:
        secret = token or self.token()
        if not secret:
            raise ConnectorAuthError(PROVIDER, "Notion is not connected")
        try:
            response = self.http.request(method, f"{NOTION_API}{path}", headers=self._headers(secret), **kwargs)
        except httpx.HTTPError as err:
            raise ConnectorError(PROVIDER, f"Could not reach Notion: {err}") from err
        if response.status_code == 401:
            if clear_on_401:
                self.secrets.delete(TOKEN_KEY)
                self.last_error = "Notion token was revoked or is invalid; paste a new one"
                self.on_change()
            raise ConnectorAuthError(PROVIDER, "Notion rejected the integration token")
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            code = str(payload.get("code") or "") if isinstance(payload, dict) else ""
            if response.status_code == 403 or code == "restricted_resource":
                raise ConnectorScopeError(
                    PROVIDER,
                    "Notion blocked this (restricted_resource). In notion.so/profile/integrations give your "
                    "integration the 'Insert content' and 'Update content' capabilities, and share the page with it "
                    "(page menu > Connections), then try again.",
                    code="needs_capabilities",
                    help_url=INTEGRATIONS_URL,
                )
            message = payload.get("message") or response.status_code
            if response.status_code == 404:
                raise ConnectorError(
                    PROVIDER,
                    "Notion error: Page not found, or not shared with the Escort integration (page menu > Connections)",
                    code="not_found",
                )
            raise ConnectorError(PROVIDER, f"Notion error: {message}")
        return payload

    # ---- connect -------------------------------------------------------
    def validate(self, token: str) -> str | None:
        """Check a token against /v1/users/me. Returns a display name for the workspace/bot."""
        me = self._call("GET", "/users/me", token=token, clear_on_401=False)
        bot = me.get("bot") or {}
        return bot.get("workspace_name") or me.get("name") or None

    def connect(self, token: str) -> str | None:
        token = token.strip()
        if not token:
            raise ConnectorSetupError(PROVIDER, "Paste a Notion integration token")
        try:
            account = self.validate(token)
        except ConnectorAuthError as err:
            raise ConnectorSetupError(PROVIDER, "Notion rejected this token; copy the integration secret again") from err
        self.secrets.set(TOKEN_KEY, json.dumps({"token": token, "account": account}))
        self.last_error = None
        self.on_change()
        return account

    def disconnect(self) -> None:
        self.secrets.delete(TOKEN_KEY)
        self.last_error = None
        self.on_change()

    # ---- content -------------------------------------------------------
    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "page_size": max(1, min(limit, 50)),
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
        if query.strip():
            body["query"] = query.strip()
        payload = self._call("POST", "/search", json=body)
        hits = []
        for page in payload.get("results") or []:
            if page.get("object") != "page" or page.get("archived") or page.get("in_trash"):
                continue
            edited = str(page.get("last_edited_time") or "")[:10]
            hits.append(
                {
                    "id": str(page.get("id")),
                    "title": page_title(page),
                    "snippet": f"Notion page · edited {edited}" if edited else "Notion page",
                    "url": page.get("url"),
                    "mock": False,
                }
            )
        return hits[:limit]

    def fetch(self, page_id: str) -> ContextItem:
        page = self._call("GET", f"/pages/{page_id}")
        title = page_title(page)
        lines: list[str] = [f"# {title}", ""]
        budget = [MAX_BLOCKS]
        self._collect(page_id, lines, depth=0, budget=budget)
        markdown = "\n".join(lines)
        return ContextItem(
            id=new_id("ctx"),
            source_kind="notion",
            source_label="Notion",
            title=title,
            sections=sections_from_markdown(markdown),
            enabled=True,
            external_id=page_id,
        )

    def _collect(self, block_id: str, lines: list[str], depth: int, budget: list[int]) -> None:
        cursor: str | None = None
        while budget[0] > 0:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self._call("GET", f"/blocks/{block_id}/children", params=params)
            for block in payload.get("results") or []:
                budget[0] -= 1
                if budget[0] < 0:
                    return
                line = block_line(block)
                if line is not None:
                    indent = "  " * depth if line and not line.startswith("#") else ""
                    lines.append(f"{indent}{line}")
                if block.get("has_children") and depth + 1 < MAX_DEPTH and block.get("type") not in {"child_page", "child_database"}:
                    self._collect(str(block.get("id")), lines, depth + 1, budget)
            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")
            if not cursor:
                return
