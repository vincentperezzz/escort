from __future__ import annotations

import re

from escort.models import DeicticHint


_REGIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("top", re.compile(r"yung nasa taas|nasa taas|sa taas|up top|at the top", re.I)),
    ("bottom", re.compile(r"nasa baba|sa baba|at the bottom|down there", re.I)),
    ("left", re.compile(r"sa kaliwa|nasa kaliwa|on the left", re.I)),
    ("right", re.compile(r"sa kanan|nasa kanan|on the right", re.I)),
    (
        "share",
        re.compile(
            r"\bito\b|\biyan\b|\biyon\b|\bthis number\b|\bthat number\b|\bthis one\b|\bthat one\b",
            re.I,
        ),
    ),
)

_NOUNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percentage", re.compile(r"percent|processing fee|\bfee\b|2\.9|2\.4|\bnumber\b", re.I)),
    ("chart", re.compile(r"\bchart\b|\bgraph\b", re.I)),
    ("button", re.compile(r"\bbutton\b", re.I)),
)


def detect_deixis(text: str) -> DeicticHint | None:
    region = None
    phrase = None
    for name, pattern in _REGIONS:
        match = pattern.search(text)
        if match:
            region = name
            phrase = match.group(0)
            break
    if region is None:
        return None
    noun = "object"
    for candidate, pattern in _NOUNS:
        if pattern.search(text):
            noun = candidate
            break
    if noun == "object" and region == "top":
        noun = "number"
    return DeicticHint(region=region, noun=noun, phrase=phrase or region)
