from __future__ import annotations

from escort.context.models import ContextSection
from escort.prd import Section, parse_sections


def sections_from_markdown(text: str) -> list[ContextSection]:
    parsed: list[Section] = parse_sections(text)
    if not parsed and text.strip():
        return [
            ContextSection(
                anchor="notes",
                heading="Notes",
                quote=text.strip()[:240],
                body=text.strip(),
            )
        ]
    out: list[ContextSection] = []
    for section in parsed:
        out.append(
            ContextSection(
                anchor=section.anchor,
                heading=section.heading,
                quote=section.quote(),
                body=section.body,
            )
        )
    return out
