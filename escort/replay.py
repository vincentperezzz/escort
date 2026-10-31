from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from escort.paths import repo_root
from escort.pipeline import Pipeline
from escort.prd import keywords_from_prd
from escort.sam.muse import DeicticSegmenter, MuseSam
from escort.settings import Settings
from escort.spark.muse import FallbackSpark, MuseSpark
from escort.spark.rules import RuleSpark
from escort.store import MeetingStore
from escort.transcribe.muse import MuseFileTranscriber
from escort.transcribe.scripted import ScriptedTranscriber
from escort.transcribe.whisper_fallback import WhisperTranscriber


@dataclass
class ScenarioUtterance:
    id: str
    speaker: str
    audio: Path
    text: str


@dataclass
class ScenarioFrame:
    id: str
    at: str
    path: Path


@dataclass
class Scenario:
    id: str
    title: str
    consent: str
    prd_path: Path
    prd_text: str
    utterances: list[ScenarioUtterance]
    frames: list[ScenarioFrame]


def load_scenario(path: Path | None = None) -> Scenario:
    root = repo_root()
    scenario_path = path or (root / "fixtures" / "scenario.json")
    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    base = scenario_path.parent.parent if scenario_path.parent.name == "fixtures" else scenario_path.parent
    if not (base / raw["prd"]).exists() and (root / raw["prd"]).exists():
        base = root
    prd_path = (base / raw["prd"]).resolve()
    return Scenario(
        id=raw["id"],
        title=raw["title"],
        consent=raw["consent"],
        prd_path=prd_path,
        prd_text=prd_path.read_text(encoding="utf-8"),
        utterances=[
            ScenarioUtterance(
                id=item["id"],
                speaker=item["speaker"],
                audio=(base / item["audio"]).resolve(),
                text=item["text"],
            )
            for item in raw["utterances"]
        ],
        frames=[
            ScenarioFrame(id=item["id"], at=item["at"], path=(base / item["path"]).resolve())
            for item in raw["frames"]
        ],
    )


def resolve_transcriber_mode(mode: str, settings: Settings) -> str:
    if mode != "auto":
        return mode
    if settings.api_key:
        return "muse"
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "scripted"
    return "whisper"


def build_transcriber_resolved(mode: str, scenario: Scenario, settings: Settings):
    chosen = resolve_transcriber_mode(mode, settings)
    if chosen == "scripted":
        return ScriptedTranscriber({item.audio.name: item.text for item in scenario.utterances})
    if chosen == "muse":
        return MuseFileTranscriber(settings, keywords=keywords_from_prd(scenario.prd_text))
    return WhisperTranscriber()


def build_spark(mode: str, settings: Settings, store: MeetingStore):
    rules = RuleSpark()
    if mode == "rules":
        return rules
    if mode == "auto" and not settings.api_key:
        return rules
    if mode == "auto":
        return FallbackSpark(MuseSpark(settings), rules, store.warn)
    return MuseSpark(settings)


def build_segmenter(mode: str, settings: Settings, store: MeetingStore) -> DeicticSegmenter:
    if mode == "fallback" or (mode == "auto" and not settings.api_key):
        return DeicticSegmenter(None)
    return DeicticSegmenter(MuseSam(settings), store.warn)


def build_pipeline(
    store: MeetingStore,
    scenario: Scenario,
    settings: Settings,
    *,
    transcriber_mode: str,
    spark_mode: str,
    segmenter_mode: str,
) -> Pipeline:
    store.title = scenario.title
    store.consent = scenario.consent
    store.prd_name = scenario.prd_path.name
    pipeline = Pipeline(
        store,
        build_transcriber_resolved(transcriber_mode, scenario, settings),
        build_spark(spark_mode, settings, store),
        build_segmenter(segmenter_mode, settings, store),
        scenario.prd_text,
    )
    store.transcriber = pipeline.transcriber.name
    store.spark = pipeline.spark.name
    store.segmenter = pipeline.segmenter.name
    return pipeline


def run_replay(pipeline: Pipeline, scenario: Scenario, *, pace_ms: int = 0, mode: str = "replay") -> dict:
    pipeline.store.mode = mode
    frames_by_utt: dict[str, list[ScenarioFrame]] = {}
    for frame in scenario.frames:
        frames_by_utt.setdefault(frame.at, []).append(frame)
    for utterance in scenario.utterances:
        for frame in frames_by_utt.get(utterance.id, []):
            pipeline.ingest_image(Image.open(frame.path).convert("RGB"))
        if pace_ms > 0:
            time.sleep(pace_ms / 1000)
        pipeline.ingest_wav(utterance.audio, speaker=utterance.speaker)
    pipeline.store.mode = "replay-done" if mode == "replay" else pipeline.store.mode
    return pipeline.store.snapshot()
