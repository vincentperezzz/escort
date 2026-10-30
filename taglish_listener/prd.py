from __future__ import annotations

import re
from dataclasses import dataclass


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
    found = [seed for seed in seeds if seed.lower() in markdown.lower()]
    return found[:limit]
