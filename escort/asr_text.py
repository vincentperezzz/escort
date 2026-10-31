from __future__ import annotations

import re

ASR_KEYWORDS = ["PRD", "QR", "KYC", "GCash", "BayaniPay", "API"]

_MISHEAR = (
    (re.compile(r"\bbayani\s+pi\b", re.I), "BayaniPay"),
    (re.compile(r"\bbayani\s+pay\b", re.I), "BayaniPay"),
    (re.compile(r"\be[\s-]?hay\b", re.I), "KYC"),
    (re.compile(r"\beerie\b", re.I), "PRD"),
    (re.compile(r"\bg[\s-]?cash\b", re.I), "GCash"),
    (re.compile(r"\bcash\b", re.I), "GCash"),
    (re.compile(r"\byouth\b", re.I), "QR"),
)

_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[^\s]")


def dedupe_adjacent(text: str) -> str:
    parts = text.split()
    kept: list[str] = []
    for part in parts:
        if kept and _token_key(part) and _token_key(part) == _token_key(kept[-1]):
            if _token_key(part) == part.lower() and _token_key(kept[-1]) != kept[-1].lower():
                kept[-1] = part
            continue
        kept.append(part)
    return " ".join(kept)


def _token_key(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def normalize_asr(text: str) -> str:
    cleaned = dedupe_adjacent(text)
    for pattern, replacement in _MISHEAR:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def fuzzy_mentions(text: str, term: str) -> bool:
    normalized = normalize_asr(text)
    if re.search(rf"\b{re.escape(term)}\b", normalized, re.I):
        return True
    if term.upper() == "PRD" and re.search(r"\bAPI\b", text):
        return True
    return False
