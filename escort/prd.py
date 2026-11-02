from __future__ import annotations

import re
from dataclasses import dataclass

from escort.asr_text import ASR_KEYWORDS


@dataclass
class Section:
    heading: str
    anchor: str
    body: str

    def quote(self) -> str:
        compact = " ".join(self.body.split())
        if not compact:
            return self.heading
        parts = re.split(r"(?<=[.])\s", compact, maxsplit=1)
        return parts[0]


def parse_sections(markdown: str) -> list[Section]:
    sections: list[Section] = []
    heading = ""
    anchor = ""
    buf: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            if heading:
                sections.append(Section(heading=heading, anchor=anchor, body="\n".join(buf).strip()))
            heading = line.lstrip("#").strip()
            match = re.match(r"(\d+(?:\.\d+)?)", heading)
            anchor = match.group(1) if match else heading
            buf = []
        elif heading:
            buf.append(line)
    if heading:
        sections.append(Section(heading=heading, anchor=anchor, body="\n".join(buf).strip()))
    return sections


def section_by_anchor(sections: list[Section], anchor: str) -> Section | None:
    for section in sections:
        if section.anchor == anchor:
            return section
    return None


_STOP_WORDS = {
    "The", "And", "For", "With", "From", "This", "That", "When", "Where", "What",
    "Who", "Why", "How", "Not", "Are", "Was", "Were", "Will", "Shall", "Can",
    "May", "Must", "Should", "Would", "Could", "Into", "Onto", "Upon", "Over",
    "Under", "After", "Before", "Between", "Through", "During", "Without", "Within",
    "Notes", "Scope", "Section", "Phase", "Summary", "Meeting", "Call", "Client",
    "Upload", "Notion", "Google", "Docs", "Sheets", "Slides", "Drive", "Untitled",
}


def keywords_from_prd(markdown: str, limit: int = 24) -> list[str]:
    seeds = [
        "BayaniPay",
        "Phase 1",
        "Phase 2",
        "InstaPay",
        "KYC",
        "payment integration",
        "processing fee",
        "card checkout",
    ]
    found = list(ASR_KEYWORDS)
    lower_md = markdown.lower()
    for seed in seeds:
        if seed.lower() in lower_md and seed not in found:
            found.append(seed)
    seen_lower = {item.lower() for item in found}
    for match in re.finditer(r"\b([A-Z][a-zA-Z0-9_-]{2,}(?:\s+[A-Z][a-zA-Z0-9_-]{2,})?)\b", markdown):
        candidate = match.group(1).strip()
        parts = candidate.split()
        if all(part in _STOP_WORDS for part in parts):
            continue
        if len(parts) == 1 and parts[0] in _STOP_WORDS:
            continue
        if candidate.lower() not in seen_lower:
            found.append(candidate)
            seen_lower.add(candidate.lower())
            if len(found) >= limit:
                break
    return found[:limit]
