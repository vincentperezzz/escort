from __future__ import annotations

from pathlib import Path


def card_body(card: dict) -> str:
    lines = [card.get("title") or card.get("type") or "Card", card.get("body") or ""]
    if card.get("prd_section"):
        lines.append(f"PRD · {card['prd_section']}")
    if card.get("latency_ms") is not None:
        lines.append(f"{card['latency_ms']} ms from the end of the line")
    return "\n".join(line for line in lines if line).strip()


def buttons_for(card: dict) -> list[tuple[str, str]]:
    card_id = str(card.get("id") or "card")
    return [
        (f"push|{card_id}", "Push back"),
        (f"note|{card_id}", "Note it"),
        (f"ignore|{card_id}", "Ignore"),
    ]


def list_rows(cards: list[dict]) -> list[dict[str, str]]:
    rows = []
    for card in cards:
        title = (card.get("title") or "Say this")[:24]
        rows.append(
            {
                "id": f"say|{card.get('id') or title}",
                "title": title,
                "description": (card.get("body") or "")[:72],
            }
        )
    return rows


def crop_file(card: dict, media_dir: Path) -> Path | None:
    url = str(card.get("crop_url") or "")
    name = url.rsplit("/", 1)[-1]
    if not name:
        return None
    path = media_dir / name
    if path.is_file():
        return path
    return None
