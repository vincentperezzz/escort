from __future__ import annotations

import re

from taglish_listener.models import CardDraft, FrameRecord, Utterance
from taglish_listener.prd import Section, parse_sections, section_by_anchor


_PHASE = re.compile(r"phase\s*1", re.I)
_PAYMENT = re.compile(r"payment integration|card checkout|instapay", re.I)
_PULL = re.compile(r"i-pull|pull|into phase|move", re.I)
_FEE = re.compile(r"i-adjust|adjust|2\.9", re.I)
_MONEY = re.compile(r"percent|fee|number|processing", re.I)
_CONFIRM = re.compile(r"i-confirm ko (?P<what>.+)", re.I)
_CHECK = re.compile(r"i-check ko (?P<what>.+)", re.I)


def _clean(text: str) -> str:
    return text.strip().rstrip(".").strip()


def infer_when(text: str) -> str:
    lowered = text.lower()
    if "after this call" in lowered:
        return "after this call"
    if "bago" in lowered:
        return "before committing"
    if "muna" in lowered:
        return "before agreeing"
    return "unspecified"


class RuleSpark:
    name = "rules-fallback"

    def review(
        self,
        *,
        prd_text: str,
        utterances: list[Utterance],
        trigger: Utterance,
        frame: FrameRecord | None = None,
    ) -> list[CardDraft]:
        del utterances, frame
        sections = parse_sections(prd_text)
        text = trigger.text
        cards: list[CardDraft] = []
        phase = section_by_anchor(sections, "2.2")
        pricing = section_by_anchor(sections, "3")
        if _PHASE.search(text) and _PAYMENT.search(text) and _PULL.search(text) and phase:
            cards.append(_phase_conflict(phase))
            cards.append(_phase_reply(phase))
        if _FEE.search(text) and _MONEY.search(text) and pricing:
            cards.append(_fee_conflict(pricing))
            cards.append(_fee_reply(pricing))
        commitment = _commitment(trigger)
        if commitment:
            cards.append(commitment)
        return cards


def _phase_conflict(section: Section) -> CardDraft:
    return CardDraft(
        type="PRD_CONFLICT",
        title="Payment integration is not Phase 1",
        body=(
            f"They want payment integration pulled into Phase 1. "
            f"{section.heading}: \"{section.quote()}\""
        ),
        prd_section=section.heading,
    )


def _fee_conflict(section: Section) -> CardDraft:
    return CardDraft(
        type="PRD_CONFLICT",
        title="That fee is not a call-time edit",
        body=(
            f"They want the number on the share changed. "
            f"{section.heading}: \"{section.quote()}\""
        ),
        prd_section=section.heading,
    )


def _phase_reply(section: Section) -> CardDraft:
    return CardDraft(
        type="SUGGESTED_REPLY",
        title="Say this next",
        body=(
            "Ah sige, let me park that. Payment integration is Phase 2 pa sa PRD, "
            f"section {section.anchor}, so hindi natin siya mahila sa Phase 1 "
            "without a change request. Okay lang ba na i-escalate ko muna?"
        ),
        prd_section=section.heading,
    )


def _fee_reply(section: Section) -> CardDraft:
    return CardDraft(
        type="SUGGESTED_REPLY",
        title="Say this about the number",
        body=(
            "Yung nasa taas, yung 2.4 percent, finance sign-off pa kailangan "
            f"bago natin galawin. {section.heading} pa yan. "
            "I-confirm ko after this call, ha?"
        ),
        prd_section=section.heading,
    )


def _commitment(trigger: Utterance) -> CardDraft | None:
    match = _CONFIRM.search(trigger.text) or _CHECK.search(trigger.text)
    if not match:
        return None
    verb = "confirm" if match.re is _CONFIRM else "check"
    what = _clean(match.group("what"))
    what = re.sub(r"^(muna|bago)\s+", "", what, flags=re.I)
    what = re.sub(r"\s+bago ako mag-commit$", "", what, flags=re.I)
    what = f"{verb} {what}".strip()
    return CardDraft(
        type="COMMITMENT",
        title="Promise on the call",
        body=f"{trigger.speaker} will {what}.",
        who=trigger.speaker,
        what=what,
        when=infer_when(trigger.text),
    )
