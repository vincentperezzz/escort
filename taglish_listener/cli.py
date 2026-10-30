from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from taglish_listener.paths import repo_root
from taglish_listener.replay import build_pipeline, load_scenario, run_replay
from taglish_listener.server import AppState, create_server
from taglish_listener.settings import load_settings
from taglish_listener.store import MeetingStore
from taglish_listener.wer import wer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="taglish-listener")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve")
    _add_runtime_flags(serve)
    serve.add_argument("--transport", choices=("stdio", "http"), default="http")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8747)
    serve.add_argument("--replay", action="store_true")
    serve.add_argument("--live", action="store_true")
    serve.add_argument("--pace-ms", type=int, default=800)

    replay = sub.add_parser("replay")
    _add_runtime_flags(replay)
    replay.add_argument("--pace-ms", type=int, default=0)

    live = sub.add_parser("live")
    _add_runtime_flags(live)
    live.add_argument("--transport", choices=("stdio", "http"), default="http")
    live.add_argument("--host", default="127.0.0.1")
    live.add_argument("--port", type=int, default=8747)

    evaluate = sub.add_parser("eval")
    evaluate.add_argument("--audio", type=Path, default=None)
    evaluate.add_argument("--reference", type=Path, default=None)
    evaluate.add_argument("--hypothesis", type=Path, default=None)
    evaluate.add_argument("--backend", default="auto", choices=("auto", "muse", "whisper", "scripted"))

    args = parser.parse_args(argv)
    if args.cmd == "eval":
        raise SystemExit(_eval(args))
    if args.cmd == "replay":
        snapshot = _prepare(args, paced=False)
        json.dump({"cards": snapshot["cards"], "backends": _backends(snapshot)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    _serve(args)


def _add_runtime_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenario", type=Path, default=None)
    parser.add_argument("--transcriber", default="scripted", choices=("auto", "muse", "whisper", "scripted"))
    parser.add_argument("--spark", default="auto", choices=("auto", "muse", "rules"))
    parser.add_argument("--segmenter", default="auto", choices=("auto", "sam", "fallback"))


def _prepare(args, *, paced: bool) -> dict:
    settings = load_settings()
    scenario = load_scenario(args.scenario)
    store = MeetingStore(repo_root() / "var" / "runtime")
    pipeline = build_pipeline(
        store,
        scenario,
        settings,
        transcriber_mode=args.transcriber,
        spark_mode=args.spark,
        segmenter_mode=args.segmenter,
    )
    pace = args.pace_ms if paced else 0
    return run_replay(pipeline, scenario, pace_ms=pace)


def _backends(snapshot: dict) -> dict:
    return {
        "transcriber": snapshot["transcriber"],
        "spark": snapshot["spark"],
        "segmenter": snapshot["segmenter"],
    }


def _serve(args) -> None:
    settings = load_settings()
    scenario = load_scenario(args.scenario)
    store = MeetingStore(repo_root() / "var" / "runtime")
    state = AppState(store)
    state.pipeline = build_pipeline(
        store,
        scenario,
        settings,
        transcriber_mode=args.transcriber,
        spark_mode=args.spark,
        segmenter_mode=args.segmenter,
    )
    server = create_server(state)
    stop = threading.Event()
    live = args.cmd == "live" or getattr(args, "live", False)
    replay = getattr(args, "replay", False)
    if live:
        from taglish_listener.live import start_live

        start_live(state.pipeline, settings, stop)
    elif replay:
        if args.transport == "stdio" or args.pace_ms <= 0:
            run_replay(state.pipeline, scenario, pace_ms=0)
        else:
            threading.Thread(
                target=run_replay,
                args=(state.pipeline, scenario),
                kwargs={"pace_ms": args.pace_ms},
                daemon=True,
            ).start()
    if args.transport == "stdio":
        server.run(transport="stdio")
        return
    server.run(transport="streamable-http", host=args.host, port=args.port, stateless_http=True)


def _eval(args) -> int:
    root = repo_root()
    reference_path = args.reference or (root / "fixtures" / "eval" / "reference.txt")
    audio_path = args.audio or (root / "fixtures" / "eval" / "taglish.wav")
    reference = reference_path.read_text(encoding="utf-8").strip()
    if args.hypothesis:
        hypothesis = args.hypothesis.read_text(encoding="utf-8").strip()
        report = {
            "backend": "hypothesis-file",
            "measures_recognition": True,
            "wer": round(wer(reference, hypothesis), 4),
            "latency_ms": None,
            "reference": reference,
            "hypothesis": hypothesis,
        }
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    settings = load_settings()
    backend = args.backend
    if backend == "auto":
        if settings.api_key:
            backend = "muse"
        else:
            try:
                import faster_whisper  # noqa: F401

                backend = "whisper"
            except ImportError:
                report = {
                    "backend": None,
                    "measures_recognition": False,
                    "wer": None,
                    "latency_ms": None,
                    "error": "Set MODEL_API_KEY for Muse Voice Transcribe or pip install 'taglish-listener[whisper]'. "
                    "Pass --hypothesis to score a transcript you already have. "
                    "The scripted fixture is not a recognition score.",
                }
                json.dump(report, sys.stdout, indent=2)
                sys.stdout.write("\n")
                return 2
    if backend == "scripted":
        report = {
            "backend": "scripted-fixture",
            "measures_recognition": False,
            "wer": 0.0,
            "latency_ms": 0,
            "reference": reference,
            "hypothesis": reference,
            "note": "Scripted mode replays the reference. It is not a Muse or Whisper score.",
        }
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    from taglish_listener.replay import load_scenario
    from taglish_listener.transcribe.muse import MuseFileTranscriber
    from taglish_listener.transcribe.whisper_fallback import WhisperTranscriber

    scenario = load_scenario()
    if backend == "muse":
        from taglish_listener.prd import keywords_from_prd

        transcriber = MuseFileTranscriber(settings, keywords=keywords_from_prd(scenario.prd_text))
    else:
        transcriber = WhisperTranscriber()
    started = time.perf_counter()
    turns = transcriber.transcribe_file(audio_path, speaker="Them")
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    hypothesis = " ".join(turn.text for turn in turns).strip()
    report = {
        "backend": transcriber.name,
        "measures_recognition": True,
        "wer": round(wer(reference, hypothesis), 4),
        "latency_ms": latency_ms,
        "reference": reference,
        "hypothesis": hypothesis,
        "audio": str(audio_path),
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0
