from __future__ import annotations

import threading
import time
from pathlib import Path

from PIL import Image

from taglish_listener.deixis import detect_deixis
from taglish_listener.diff import FrameGate
from taglish_listener.models import CARD_TYPES, CardDraft, CardRecord, Turn, Utterance
from taglish_listener.store import MeetingStore, new_id, utc_now


class Pipeline:
    def __init__(self, store: MeetingStore, transcriber, spark, segmenter, prd_text: str) -> None:
        self.store = store
        self.transcriber = transcriber
        self.spark = spark
        self.segmenter = segmenter
        self.prd_text = prd_text
        self.gate = FrameGate()
        self._sparked_utt: str | None = None
        self._sparked_frame: str | None = None
        self.store.transcriber = getattr(transcriber, "name", "")
        self.store.spark = getattr(spark, "name", "")
        self.store.segmenter = getattr(segmenter, "name", "")

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
        finished_at = time.perf_counter()
        utterance = self.store.add_utterance(
            speaker=turn.speaker,
            text=turn.text,
            start_ms=turn.start_ms,
            end_ms=turn.end_ms,
            finished_at=finished_at,
        )
        self._emit(utterance)
        return utterance

    def note_partial(self, speaker: str, text: str) -> None:
        self.store.set_partial(speaker, text)

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
        hint = detect_deixis(utterance.text)
        crop_path = None
        crop_url = None
        segmenter_name = None
        if hint is not None and frame is not None:
            image = Image.open(frame.path)
            cropped = self.segmenter.crop(image, hint)
            path = self.store.save_png(cropped.image, "crop")
            crop_path = str(path)
            crop_url = f"/panel/media/{path.name}"
            segmenter_name = cropped.source
            self.store.segmenter = getattr(self.segmenter, "name", segmenter_name)
        for draft in drafts:
            if draft.type not in CARD_TYPES:
                continue
            if not self.store.claim(_dedupe_key(draft)):
                continue
            self.store.add_card(
                CardRecord(
                    id=new_id("card"),
                    type=draft.type,
                    title=draft.title,
                    body=draft.body,
                    prd_section=draft.prd_section,
                    who=draft.who,
                    what=draft.what,
                    utterance_id=utterance.id,
                    trigger_text=utterance.text,
                    created_at=utc_now(),
                    latency_ms=round((time.perf_counter() - utterance.finished_at) * 1000, 1),
                    crop_path=crop_path if hint is not None else None,
                    crop_url=crop_url if hint is not None else None,
                    source=getattr(self.spark, "name", "spark"),
                    deictic=hint.phrase if hint is not None else None,
                    segmenter=segmenter_name,
                )
            )
        self._sparked_utt = utterance.id
        self._sparked_frame = frame.id if frame else None

    def push_suggestion(
        self,
        text: str,
        *,
        title: str = "Say this",
        card_type: str = "SUGGESTED_REPLY",
        prd_section: str | None = None,
        who: str | None = None,
        what: str | None = None,
        utterance_id: str | None = None,
    ) -> CardRecord:
        if card_type not in CARD_TYPES:
            raise ValueError(card_type)
        if card_type == "PRD_CONFLICT" and not (prd_section or "").strip():
            raise ValueError("PRD_CONFLICT needs prd_section")
        trigger = self.store.utterance(utterance_id) if utterance_id else self.store.latest_utterance()
        finished = trigger.finished_at if trigger else time.perf_counter()
        card = CardRecord(
            id=new_id("card"),
            type=card_type,
            title=title,
            body=text.strip(),
            prd_section=prd_section,
            who=who,
            what=what,
            utterance_id=trigger.id if trigger else None,
            trigger_text=trigger.text if trigger else None,
            created_at=utc_now(),
            latency_ms=round(max(0.0, time.perf_counter() - finished) * 1000, 1),
            crop_path=None,
            crop_url=None,
            source="assistant",
            deictic=None,
            segmenter=None,
        )
        return self.store.add_card(card)


def _dedupe_key(draft: CardDraft) -> tuple:
    if draft.type == "PRD_CONFLICT":
        return (draft.type, draft.prd_section)
    if draft.type == "COMMITMENT":
        return (draft.type, draft.who, (draft.what or "")[:80])
    return (draft.type, draft.body)
