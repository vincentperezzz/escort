from __future__ import annotations

from pathlib import Path

import httpx

from escort.whatsapp.config import WhatsAppConfig


class GraphClient:
    def __init__(self, config: WhatsAppConfig, http: httpx.Client | None = None) -> None:
        self.config = config
        self.http = http or httpx.Client(timeout=30)
        self.root = f"https://graph.facebook.com/{config.graph_version}"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.access_token}"}

    def send(self, payload: dict) -> dict:
        response = self.http.post(
            f"{self.root}/{self.config.phone_number_id}/messages",
            headers={**self._auth(), "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def send_text(self, to: str, body: str) -> dict:
        return self.send(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": body[:4096]},
            }
        )

    def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
        return self.send(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:1024]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": button_id[:256], "title": title[:20]}}
                            for button_id, title in buttons[:3]
                        ]
                    },
                },
            }
        )

    def send_list(self, to: str, body: str, rows: list[dict[str, str]]) -> dict:
        return self.send(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": body[:1024]},
                    "action": {
                        "button": "Replies",
                        "sections": [
                            {
                                "title": "Say this",
                                "rows": [
                                    {
                                        "id": row["id"][:200],
                                        "title": row["title"][:24],
                                        "description": row.get("description", "")[:72],
                                    }
                                    for row in rows[:10]
                                ],
                            }
                        ],
                    },
                },
            }
        )

    def upload_media(self, path: Path, mime: str = "image/png") -> str:
        with path.open("rb") as handle:
            response = self.http.post(
                f"{self.root}/{self.config.phone_number_id}/media",
                headers=self._auth(),
                data={"messaging_product": "whatsapp"},
                files={"file": (path.name, handle, mime)},
            )
        response.raise_for_status()
        return str(response.json()["id"])

    def send_image(self, to: str, media_id: str, caption: str = "") -> dict:
        image: dict[str, str] = {"id": media_id}
        if caption:
            image["caption"] = caption[:1024]
        return self.send(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "image",
                "image": image,
            }
        )

    def download_media(self, media_id: str) -> tuple[str, bytes]:
        meta = self.http.get(f"{self.root}/{media_id}", headers=self._auth())
        meta.raise_for_status()
        url = meta.json()["url"]
        blob = self.http.get(url, headers=self._auth())
        blob.raise_for_status()
        filename = media_id
        disposition = blob.headers.get("content-disposition", "")
        if "filename=" in disposition:
            filename = disposition.split("filename=", 1)[1].strip('"')
        return filename, blob.content
