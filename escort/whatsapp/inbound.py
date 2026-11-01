from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Inbound:
    sender: str
    kind: str
    text: str = ""
    action_id: str = ""
    media_id: str = ""
    filename: str = ""
    mime: str = ""


def parse_messages(payload: dict) -> list[Inbound]:
    found: list[Inbound] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                item = _one(message)
                if item is not None:
                    found.append(item)
    return found


def _one(message: dict) -> Inbound | None:
    sender = str(message.get("from") or "")
    kind = message.get("type") or ""
    if kind == "text":
        return Inbound(sender=sender, kind="text", text=(message.get("text") or {}).get("body") or "")
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return Inbound(
            sender=sender,
            kind="action",
            text=reply.get("title") or "",
            action_id=reply.get("id") or "",
        )
    if kind == "document":
        document = message.get("document") or {}
        return Inbound(
            sender=sender,
            kind="document",
            media_id=document.get("id") or "",
            filename=document.get("filename") or "upload.md",
            mime=document.get("mime_type") or "",
        )
    if kind == "image":
        image = message.get("image") or {}
        return Inbound(
            sender=sender,
            kind="image",
            text=image.get("caption") or "",
            media_id=image.get("id") or "",
            mime=image.get("mime_type") or "image/jpeg",
        )
    return None
