from __future__ import annotations

import threading
import time
from pathlib import Path

from PIL import Image

from escort.deixis import detect_deixis
from escort.diff import FrameGate
from escort.models import CARD_TYPES, CardDraft, CardRecord, Turn, Utterance
from escort.prd import parse_sections
from escort.sam.fallback import crop_share
from escort.spark.rules import RuleSpark, infer_when
from escort.store import MeetingStore, new_id, utc_now


class Pipeline:
    def __init__(self, store: MeetingStore, transcriber, spark, segmenter, prd_text: str) -> None:
        self.store = store
        self.transcriber = transcriber
        self.spark = spark
        self.segmenter = segmenter
        self.prd_text = prd_text
        self.fast = RuleSpark()
        self.gate = FrameGate()
        self._sparked_utt: str | None = None
        self._sparked_frame: str | None = None
        self.store.transcriber = getattr(transcriber, "name", "")
        self.store.spark = getattr(spark, "name", "")
        self.store.segmenter = getattr(segmenter, "name", "")
        self.store.prd_sections = [
            {"anchor": section.anchor, "heading": section.heading, "quote": section.quote()}
            for section in parse_sections(prd_text)
        ]

    def ingest_image(self, image: Image.Image) -> bool:
        changed, digest = self.gate.changed(image)
        if not changed:
            self.store.note_skipped_frame()
            return False
        path = self.store.save_png(image, "frame")
        self.store.add_frame(path=path, phash=digest)
        return True

    def ingest_wav(self, path: Path, *, speaker: str) -> list[Utterance]:
        turns = self.transcriber.transcribe_file(path, speaker=speaker)
        return [self.finish_turn(turn) for turn in turns if turn.text.strip()]

    def finish_turn(self, turn: Turn) -> Utterance:
        self.note_partial(turn.speaker, turn.text)
        finished_at = time.perf_counter()
        utterance = self.store.add_utterance(
            speaker=turn.speaker,
            text=turn.text,
            start_ms=turn.start_ms,
            end_ms=turn.end_ms,
            finished_at=finished_at,
        )
        self._bind_early_cards(utterance)
        self._emit(utterance)
        return utterance

    def note_partial(self, speaker: str, text: str) -> None:
        cleaned = text.strip()
        self.store.set_partial(speaker, cleaned)
        if len(cleaned) < 20:
            return
        ghost = Utterance(
            id="",
            speaker=speaker,
            text=cleaned,
            start_ms=0,
            end_ms=0,
            finished_at=time.perf_counter(),
            created_at=utc_now(),
        )
        drafts = [
            draft
            for draft in self.fast.review(
                prd_text=self.prd_text,
                utterances=[],
                trigger=ghost,
            )
            if draft.type == "PRD_CONFLICT"
        ]
        self._land(ghost, drafts, proactive=True, source=self.fast.name)

    def review_if_stale(self) -> None:
        latest = self.store.latest_utterance()
        frame = self.store.latest_frame()
        frame_id = frame.id if frame else None
        if latest is None:
            return
        if latest.id == self._sparked_utt and frame_id == self._sparked_frame:
            return
        self._emit(latest)

    def start_interval(self, seconds: float, stop: threading.Event) -> None:
        def loop() -> None:
            while not stop.wait(seconds):
                try:
                    self.review_if_stale()
                except Exception as exc:
                    self.store.warn(f"spark interval failed: {exc}")

        threading.Thread(target=loop, name="spark-interval", daemon=True).start()

    def _emit(self, utterance: Utterance) -> None:
        frame = self.store.latest_frame()
        with self.store.lock:
            window = list(self.store.utterances)
        drafts = self.spark.review(
            prd_text=self.prd_text,
            utterances=window,
            trigger=utterance,
            frame=frame,
        )
        self.store.spark = getattr(self.spark, "name", self.store.spark)
        self._land(utterance, drafts, proactive=False, source=getattr(self.spark, "name", "spark"))
        self._sparked_utt = utterance.id
        self._sparked_frame = frame.id if frame else None

    def _bind_early_cards(self, utterance: Utterance) -> None:
        with self.store.lock:
            for card in self.store.cards:
                if not card.proactive or card.utterance_id:
                    continue
                trigger = card.trigger_text or ""
                if trigger and trigger not in utterance.text and utterance.text not in trigger:
                    continue
                card.utterance_id = utterance.id
                card.trigger_text = utterance.text
                card.latency_ms = round(max(0.0, time.perf_counter() - utterance.finished_at) * 1000, 1)

    def _citation_for_heading(self, heading: str) -> str | None:
        for section in self.store.prd_sections:
            if section.get("heading") == heading and section.get("source_label"):
                return str(section["source_label"])
        return None

    def _enrich_draft_citations(self, drafts: list[CardDraft]) -> None:
        for draft in drafts:
            if not draft.prd_section or not draft.prd_section.startswith("Notes · "):
                continue
            heading = draft.prd_section.removeprefix("Notes · ")
            label = self._citation_for_heading(heading)
            if label:
                draft.prd_section = label

    def _land(self, utterance: Utterance, drafts: list[CardDraft], *, proactive: bool, source: str) -> None:
        self._enrich_draft_citations(drafts)
        frame = self.store.latest_frame()
        evidence = None
        if any(draft.type == "PRD_CONFLICT" for draft in drafts):
            evidence = self._crop_evidence(utterance.text, frame)
        for draft in drafts:
            if draft.type not in CARD_TYPES:
                continue
            if not self.store.claim(_dedupe_key(draft)):
                continue
            attached = evidence if draft.type == "PRD_CONFLICT" else None
            self.store.add_card(
                CardRecord(
                    id=new_id("card"),
                    type=draft.type,
                    title=draft.title,
                    body=draft.body,
                    prd_section=draft.prd_section,
                    who=draft.who,
                    what=draft.what,
                    utterance_id=utterance.id or None,
                    trigger_text=utterance.text,
                    created_at=utc_now(),
                    latency_ms=0.0
                    if proactive
                    else round((time.perf_counter() - utterance.finished_at) * 1000, 1),
                    crop_path=attached[0] if attached else None,
                    crop_url=attached[1] if attached else None,
                    source=source,
                    deictic=attached[2] if attached else None,
                    segmenter=attached[3] if attached else None,
                    when=draft.when,
                    proactive=proactive,
                )
            )

    def _crop_evidence(self, text: str, frame) -> tuple[str, str, str | None, str] | None:
        if frame is None:
            return None
        image = Image.open(frame.path)
        hint = detect_deixis(text)
        if hint is None:
            cropped = crop_share(image, "share")
            source = "share-crop-fallback"
            phrase = None
        else:
            cropped = self.segmenter.crop(image, hint).image
            source = self.segmenter.name
            phrase = hint.phrase
            self.store.segmenter = source
        path = self.store.save_png(cropped, "crop")
        return str(path), f"/panel/media/{path.name}", phrase, source

    def push_suggestion(
        self,
        text: str,
        *,
        title: str = "Say this",
        card_type: str = "SUGGESTED_REPLY",
        prd_section: str | None = None,
        who: str | None = None,
        what: str | None = None,
        when: str | None = None,
        utterance_id: str | None = None,
    ) -> CardRecord:
        if card_type not in CARD_TYPES:
            raise ValueError(card_type)
        if card_type == "PRD_CONFLICT" and not (prd_section or "").strip():
            raise ValueError("PRD_CONFLICT needs prd_section")
        trigger = self.store.utterance(utterance_id) if utterance_id else self.store.latest_utterance()
        finished = trigger.finished_at if trigger else time.perf_counter()
        if card_type == "COMMITMENT" and not when:
            when = infer_when(text)
        evidence = self._crop_evidence(trigger.text if trigger else text, self.store.latest_frame()) if card_type == "PRD_CONFLICT" else None
        return self.store.add_card(
            CardRecord(
                id=new_id("card"),
                type=card_type,
                title=title,
                body=text.strip(),
                prd_section=prd_section,
                who=who,
                what=what,
                utterance_id=trigger.id if trigger else None,
                trigger_text=trigger.text if trigger else text.strip(),
                created_at=utc_now(),
                latency_ms=round(max(0.0, time.perf_counter() - finished) * 1000, 1),
                crop_path=evidence[0] if evidence else None,
                crop_url=evidence[1] if evidence else None,
                source="assistant",
                deictic=evidence[2] if evidence else None,
                segmenter=evidence[3] if evidence else None,
                when=when,
                proactive=False,
            )
        )

    def what_did_i_miss(self) -> dict:
        meeting = self.store.meeting()
        lines: list[str] = []
        for item in meeting["open_items"]:
            section = item["prd_section"] or "the PRD"
            lines.append(f"{item['title']} is still open against {section}.")
        for item in meeting["commitments"]:
            lines.append(f"{item['who']} will {item['what']} ({item['when']}).")
        summary = " ".join(lines) if lines else "Nothing open yet. No promises on the table."
        return {
            "summary": summary,
            "open_items": meeting["open_items"],
            "commitments": meeting["commitments"],
        }


def _dedupe_key(draft: CardDraft) -> tuple:
    if draft.type == "PRD_CONFLICT":
        return (draft.type, draft.prd_section)
    if draft.type == "COMMITMENT":
        return (draft.type, draft.who, (draft.what or "")[:80])
    return (draft.type, draft.body)
