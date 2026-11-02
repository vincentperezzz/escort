from __future__ import annotations

import difflib
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


def normalize_asr(text: str, known_names: list[str] | None = None) -> str:
    cleaned = dedupe_adjacent(text)
    for pattern, replacement in _MISHEAR:
        cleaned = pattern.sub(replacement, cleaned)
    if known_names:
        cleaned = correct_names(cleaned, known_names)
    return cleaned


_PHONETIC_INITIALS = {
    "c": {"c", "k", "s"},
    "k": {"k", "c"},
    "s": {"s", "c", "z"},
    "z": {"z", "s"},
    "f": {"f", "p"},
    "p": {"p", "f"},
    "v": {"v", "b"},
    "b": {"b", "v"},
    "j": {"j", "g"},
    "g": {"g", "j"},
}


def _same_initial_sound(a: str, b: str) -> bool:
    if not a or not b:
        return False
    ca, cb = a[0].lower(), b[0].lower()
    return cb in _PHONETIC_INITIALS.get(ca, {ca})


def correct_names(text: str, known_names: list[str]) -> str:
    """Fix misspelled or phonetically misheard proper names against known names from context/screen."""
    candidates = [name.strip() for name in known_names if isinstance(name, str) and len(name.strip()) >= 4 and " " not in name.strip()]
    if not candidates:
        return text

    def _replace_word(match: re.Match[str]) -> str:
        word = match.group(0)
        if len(word) < 4 or not word[0].isupper():
            return word
        lower = word.lower()
        for cand in candidates:
            if cand.lower() == lower:
                return cand
        best_cand = None
        best_score = 0.0
        for cand in candidates:
            if not _same_initial_sound(word, cand):
                continue
            if abs(len(word) - len(cand)) > 2:
                continue
            score = difflib.SequenceMatcher(None, lower, cand.lower()).ratio()
            if score >= 0.82 and score > best_score:
                best_score = score
                best_cand = cand
        return best_cand if best_cand is not None else word

    return re.sub(r"\b[A-Z][a-zA-Z0-9_-]{3,}\b", _replace_word, text)


def fuzzy_mentions(text: str, term: str) -> bool:
    normalized = normalize_asr(text)
    if re.search(rf"\b{re.escape(term)}\b", normalized, re.I):
        return True
    if term.upper() == "PRD" and re.search(r"\bAPI\b", text):
        return True
    return False
