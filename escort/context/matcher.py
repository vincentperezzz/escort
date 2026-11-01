from __future__ import annotations

import re

from escort.context.models import CalendarEvent, ContextItem, MatchProposal


def _tokens(text: str) -> set[str]:
    return {part.lower() for part in re.findall(r"[a-z0-9]{3,}", text.lower())}


def match_event(event: CalendarEvent, items: list[ContextItem]) -> list[MatchProposal]:
    hay = " ".join([event.title, event.description, *event.attendees, *event.linked_doc_hints])
    event_tokens = _tokens(hay)
    proposals: list[MatchProposal] = []
    for item in items:
        if not item.enabled:
            continue
        blob = " ".join([item.title, item.source_label, *(section.heading for section in item.sections)])
        item_tokens = _tokens(blob)
        overlap = event_tokens & item_tokens
        score = len(overlap)
        for hint in event.linked_doc_hints:
            if hint.lower() in item.title.lower() or hint.lower() in blob.lower():
                score += 2
        if "bayani" in event.title.lower() and "bayani" in blob.lower():
            score += 3
        if "phase" in event.title.lower() and any("phase" in section.heading.lower() for section in item.sections):
            score += 2
        if score <= 0:
            continue
        reason = f"Matched on {', '.join(sorted(overlap)[:4])}" if overlap else f"Matched hint in {item.source_label}"
        proposals.append(
            MatchProposal(
                item_id=item.id,
                title=item.title,
                source_label=item.source_label,
                score=float(score),
                reason=reason,
            )
        )
    proposals.sort(key=lambda item: item.score, reverse=True)
    return proposals
