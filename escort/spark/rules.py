from __future__ import annotations

import re

from escort.asr_text import normalize_asr
from escort.models import CardDraft, FrameRecord, Utterance
from escort.prd import Section, parse_sections, section_by_anchor


_PHASE = re.compile(r"phase\s*1", re.I)
_PAYMENT = re.compile(r"payment integration|card checkout|instapay|gcash", re.I)
_PULL = re.compile(r"i-pull|pull|into phase|move", re.I)
_FEE = re.compile(r"i-adjust|adjust|2\.9", re.I)
_MONEY = re.compile(r"percent|fee|number|processing", re.I)
_CONFIRM = re.compile(r"i-confirm ko (?P<what>.+)", re.I)
_CHECK = re.compile(r"i-check ko (?P<what>.+)", re.I)
_EN_COMMIT = re.compile(
    r"\b(?:i'll|i\s+will|we'll|we\s+will|let\s+me)\s+(?P<verb>confirm|check|send|share|follow\s+up(?:\s+on)?|prepare|update|draft|review|schedule)\s+(?P<what>[^.?!;]+)",
    re.I,
)
_PERCENT_NUM = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
_PHASE2_RULE = re.compile(r"phase\s*2|not\s+(?:in\s+)?phase\s*1|out\s+of\s+scope", re.I)


def _clean(text: str) -> str:
    return text.strip().rstrip(".").strip()


def infer_when(text: str) -> str:
    lowered = text.lower()
    if "after this call" in lowered:
        return "after this call"
    if "tomorrow" in lowered:
        return "tomorrow"
    if "today" in lowered or "tonight" in lowered:
        return "today"
    if "bago" in lowered or "before committing" in lowered:
        return "before committing"
    if "muna" in lowered or "before agreeing" in lowered:
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
        del frame
        sections = parse_sections(prd_text)
        text = normalize_asr(trigger.text)
        cards: list[CardDraft] = []
        phase = section_by_anchor(sections, "2.2")
        pricing = section_by_anchor(sections, "3")
        if _PHASE.search(text) and _PAYMENT.search(text) and _PULL.search(text) and phase:
            cards.append(_phase_conflict(phase))
            cards.append(_phase_reply(phase))
        if _FEE.search(text) and _MONEY.search(text) and pricing:
            cards.append(_fee_conflict(pricing))
            cards.append(_fee_reply(pricing))
        if not cards and utterances:
            cards.extend(_intra_meeting_conflicts(utterances, trigger, text))
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
        prd_section=f"Notes · {section.heading}",
    )


def _fee_conflict(section: Section) -> CardDraft:
    return CardDraft(
        type="PRD_CONFLICT",
        title="That fee is not a call-time edit",
        body=(
            f"They want the number on the share changed. "
            f"{section.heading}: \"{section.quote()}\""
        ),
        prd_section=f"Notes · {section.heading}",
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
        prd_section=f"Notes · {section.heading}",
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
        prd_section=f"Notes · {section.heading}",
    )


def _intra_meeting_conflicts(
    utterances: list[Utterance], trigger: Utterance, text: str
) -> list[CardDraft]:
    prior_turns = [u for u in utterances if u.id != trigger.id and u.text != trigger.text]
    if not prior_turns:
        return []
    out: list[CardDraft] = []
    if _PHASE.search(text) and _PULL.search(text):
        for prev in prior_turns:
            prev_norm = normalize_asr(prev.text)
            if _PHASE2_RULE.search(prev_norm):
                out.append(
                    CardDraft(
                        type="PRD_CONFLICT",
                        title="Conflicts with earlier Phase 1 scope",
                        body=(
                            f"Earlier in this call ({prev.speaker}): \"{prev.text.strip()}\" — "
                            f"now {trigger.speaker} is asking to pull it into Phase 1."
                        ),
                        prd_section="Earlier in this call",
                    )
                )
                out.append(
                    CardDraft(
                        type="SUGGESTED_REPLY",
                        title="Clarify the earlier agreement",
                        body="Hold on — earlier in the call we agreed that stays in Phase 2 so Phase 1 stays on schedule. Should we keep that boundary?",
                        prd_section="Earlier in this call",
                    )
                )
                return out

    curr_nums = [m.group("num") for m in _PERCENT_NUM.finditer(text)]
    if curr_nums or (_FEE.search(text) and _MONEY.search(text)):
        for prev in prior_turns:
            prev_norm = normalize_asr(prev.text)
            prev_nums = [m.group("num") for m in _PERCENT_NUM.finditer(prev_norm)]
            if prev_nums and ((curr_nums and curr_nums[0] != prev_nums[0]) or not curr_nums):
                new_label = f"{curr_nums[0]}%" if curr_nums else "a different rate"
                out.append(
                    CardDraft(
                        type="PRD_CONFLICT",
                        title=f"Number shifted from {prev_nums[0]}% to {new_label}",
                        body=(
                            f"Earlier in this call ({prev.speaker}) stated {prev_nums[0]}% (\"{prev.text.strip()}\"), "
                            f"which does not align with {new_label} mentioned now."
                        ),
                        prd_section="Earlier in this call",
                    )
                )
                out.append(
                    CardDraft(
                        type="SUGGESTED_REPLY",
                        title="Check the earlier number",
                        body=f"Just to align — earlier in the call we said {prev_nums[0]}%. Are we changing that to {new_label} or keeping {prev_nums[0]}%?",
                        prd_section="Earlier in this call",
                    )
                )
                return out
    return out


def _commitment(trigger: Utterance) -> CardDraft | None:
    match = _CONFIRM.search(trigger.text) or _CHECK.search(trigger.text)
    if match:
        verb = "confirm" if match.re is _CONFIRM else "check"
        what = _clean(match.group("what"))
        what = re.sub(r"^(muna|bago)\s+", "", what, flags=re.I)
        what = re.sub(r"\s+bago ako mag-commit$", "", what, flags=re.I)
        what = f"{verb} {what}".strip()
    else:
        en_match = _EN_COMMIT.search(trigger.text)
        if not en_match:
            return None
        verb = re.sub(r"\s+", " ", en_match.group("verb").strip().lower())
        what_tail = _clean(en_match.group("what"))
        if not what_tail:
            return None
        what = f"{verb} {what_tail}".strip()
    return CardDraft(
        type="COMMITMENT",
        title="Promise on the call",
        body=f"{trigger.speaker} will {what}.",
        who=trigger.speaker,
        what=what,
        when=infer_when(trigger.text),
    )
