import asyncio
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from starlette.testclient import TestClient

from escort.audio_util import pcm_to_mono_24k
from escort.capture.windows_audio import choose_loopback_device, choose_mic_device
from escort.deixis import detect_deixis
from escort.diff import FrameGate, dhash, hamming
from escort.paths import repo_root
from escort.pipeline import Pipeline
from escort.replay import build_pipeline, load_scenario, run_replay
from escort.sam.fallback import crop_share, share_rect
from escort.sam.muse import DeicticSegmenter, MuseSam
from escort.sam.parse import parse_boxes, pick_box
from escort.server import AppState, create_server
from escort.settings import Settings
from escort.spark.muse import MuseSpark, build_spark_request, parse_spark_response
from escort.spark.rules import RuleSpark
from escort.store import MeetingStore
from escort.transcribe.events import AsrReducer, file_request, handshake_frame, turns_from_file_payload
from escort.transcribe.muse import MuseFileTranscriber
from escort.wer import wer


ROOT = repo_root()


def settings(key: str | None = None) -> Settings:
    return Settings(
        api_key=key,
        base_url="https://api.meta.ai/v1",
        asr_model="muse-voice-transcribe-1.0",
        spark_model="muse-spark-1.3",
        sam_model="sam-3.1",
        language_bias=("Tagalog", "English"),
        spark_interval_s=10,
    )


def test_wer_substitution_and_exact():
    assert wer("phase one payment", "phase one payment") == 0
    assert wer("pull payment into phase", "pull checkout into phase") == 0.25


def test_frame_gate_skips_identical_and_keeps_a_settled_change():
    base = Image.new("RGB", (80, 40), (20, 20, 20))
    same = base.copy()
    changed = base.copy()
    changed.paste(Image.new("RGB", (40, 40), (240, 180, 40)), (0, 0))
    gate = FrameGate()
    first, _ = gate.changed(base)
    second, _ = gate.changed(same)
    third, _ = gate.changed(changed)
    fourth, _ = gate.changed(changed.copy())
    assert first is True
    assert second is False
    assert third is False  # held as a pending candidate until the screen settles
    assert fourth is True
    assert hamming(dhash(base), dhash(same)) == 0


def test_deixis_taglish_phrases():
    top = detect_deixis("Yung nasa taas, i-adjust natin yung number")
    assert top is not None
    assert top.region == "top"
    assert top.noun == "percentage"
    pointed = detect_deixis("ito yung button")
    assert pointed is not None
    assert pointed.region == "share"
    assert pointed.noun == "button"
    assert detect_deixis("yung payment integration into Phase 1") is None


def test_sam_box_parser_picks_the_top_match():
    sample = (
        "<0f>0<|box;x1=10;y1=10;x2=40;y2=30;w=100;h=100|>,"
        "1<|box;x1=10;y1=70;x2=40;y2=90;w=100;h=100;c=0.91|>"
    )
    boxes = parse_boxes(sample)
    assert len(boxes) == 2
    assert boxes[1].score == pytest.approx(0.91)
    chosen = pick_box(boxes, "top")
    assert chosen is not None
    assert chosen.y1 == 10


def test_share_crop_keeps_the_fee_and_drops_the_bottom():
    image = Image.open(ROOT / "fixtures" / "frames" / "03-share-highlight.png")
    left, top, _right, _bottom = share_rect(image.width, image.height, "top")
    _bl, bottom_top, _br, _bb = share_rect(image.width, image.height, "bottom")
    assert image.getpixel((200, 160))[0] > 200
    assert top < 160 < bottom_top
    crop = crop_share(image, "top")
    assert crop.getpixel((200 - left, 160 - top))[0] > 200
    assert bottom_top > 210


def test_rule_spark_cites_the_prd_and_emits_each_card_type(tmp_path: Path):
    scenario = load_scenario()
    store = MeetingStore(tmp_path)
    pipeline = build_pipeline(
        store,
        scenario,
        settings(),
        transcriber_mode="scripted",
        spark_mode="rules",
        segmenter_mode="fallback",
    )
    snapshot = run_replay(pipeline, scenario, pace_ms=0)
    types = {card["type"] for card in snapshot["cards"]}
    assert types == {"PRD_CONFLICT", "COMMITMENT", "SUGGESTED_REPLY"}
    conflict = next(card for card in snapshot["cards"] if "Phase 2" in (card["prd_section"] or ""))
    assert "2.2" in conflict["prd_section"]
    assert conflict["crop_url"]
    assert conflict["latency_ms"] <= 5000
    assert snapshot["latency"]["p50_ms"] is not None
    assert snapshot["latency"]["p95_ms"] is not None
    assert snapshot["latency"]["over_target"] == 0
    assert len(snapshot["frames"]) == 1
    assert snapshot["meeting"]["open_items"]
    assert snapshot["meeting"]["commitments"]
    assert all(item["when"] for item in snapshot["meeting"]["commitments"])
    fee = next(
        card
        for card in snapshot["cards"]
        if card["prd_section"] and ("Pricing" in card["prd_section"] or card["prd_section"].startswith("3."))
    )
    assert "2.4" in fee["body"] or "finance" in fee["body"].lower()
    cropped = [card for card in snapshot["cards"] if card["deictic"] and "taas" in card["deictic"].lower()]
    assert cropped
    assert cropped[0]["crop_url"]
    assert cropped[0]["segmenter"] == "share-crop-fallback"
    assert snapshot["skipped_frames"] >= 1
    assert any(card["who"] == "Me" and card["what"] for card in snapshot["cards"])
    before = len(snapshot["cards"])
    pipeline.review_if_stale()
    assert len(store.cards) == before


def test_partial_pushes_a_conflict_before_the_turn_ends(tmp_path: Path):
    scenario = load_scenario()
    store = MeetingStore(tmp_path)
    pipeline = build_pipeline(
        store,
        scenario,
        settings(),
        transcriber_mode="scripted",
        spark_mode="rules",
        segmenter_mode="fallback",
    )
    pipeline.ingest_image(Image.open(scenario.frames[0].path))
    pipeline.note_partial(
        "Them",
        "Pwede ba nating i-pull yung payment integration into Phase 1?",
    )
    assert store.utterances == []
    early = [card for card in store.cards if card.type == "PRD_CONFLICT"]
    assert len(early) == 1
    assert early[0].proactive is True
    assert "2.2" in (early[0].prd_section or "")
    assert early[0].crop_url
    from escort.models import Turn

    pipeline.finish_turn(
        Turn(
            speaker="Them",
            text="Pwede ba nating i-pull yung payment integration into Phase 1? Kailangan namin yung card checkout.",
        )
    )
    conflicts = [card for card in store.cards if card.type == "PRD_CONFLICT" and "2.2" in (card.prd_section or "")]
    assert len(conflicts) == 1
    assert conflicts[0].utterance_id
    assert conflicts[0].latency_ms <= 5000


def test_latency_covers_the_spark_call(tmp_path: Path):
    scenario = load_scenario()
    store = MeetingStore(tmp_path)

    class SlowSpark(RuleSpark):
        name = "slow-rules"

        def review(self, **kwargs):
            import time

            time.sleep(0.05)
            return super().review(**kwargs)

    pipeline = Pipeline(
        store,
        build_pipeline(
            store,
            scenario,
            settings(),
            transcriber_mode="scripted",
            spark_mode="rules",
            segmenter_mode="fallback",
        ).transcriber,
        SlowSpark(),
        DeicticSegmenter(None),
        scenario.prd_text,
    )
    store.title = scenario.title
    run_replay(pipeline, scenario, pace_ms=0)
    assert max(card.latency_ms for card in store.cards) >= 40


def test_handshake_and_reducer_keep_the_stream_speaker():
    frame = handshake_frame(
        "secret-key",
        model="muse-voice-transcribe-1.0",
        mode="ENDPOINTING",
        language_bias=["Tagalog", "English"],
        keywords=["BayaniPay", "Phase 1"],
    )
    assert frame["model"] == "muse-voice-transcribe-1.0"
    assert frame["audioEncoding"] == "PCM_24KHZ"
    assert frame["languageBias"] == ["Tagalog", "English"]
    assert frame["authorization"]["accessToken"] == "Bearer secret-key"
    request = file_request(
        model="muse-voice-transcribe-1.0",
        mode="ENDPOINTING",
        language_bias=["Tagalog", "English"],
        keywords=["InstaPay"],
    )
    assert request["audioEncoding"] == "WAV"
    reducer = AsrReducer("Them")
    assert reducer.apply({"type": "speechStart", "turnId": 3, "audioProcessedMs": 100}) is None
    assert reducer.apply({"type": "transcript", "transcript": "yung nasa", "final": False, "audioProcessedMs": 200}) is None
    reducer.apply({"type": "speaker", "label": "A", "audioProcessedMs": 220})
    turn = reducer.apply(
        {"type": "speechComplete", "turnId": 3, "transcript": "Yung nasa taas.", "audioProcessedMs": 900}
    )
    assert turn is not None
    assert turn.speaker == "Them"
    assert turn.vendor_speaker == "A"
    assert turn.text == "Yung nasa taas."


def test_muse_transcribe_posts_the_documented_endpoint(tmp_path: Path):
    wav = ROOT / "fixtures" / "eval" / "taglish.wav"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "transcript": "ignored whole-file text",
                "audioDurationMs": 1200,
                "turns": [
                    {
                        "turnId": 1,
                        "startMs": 0,
                        "endMs": 1200,
                        "transcript": "Pwede ba",
                        "speaker": "B",
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transcriber = MuseFileTranscriber(settings("LLM_test"), client=client, keywords=["BayaniPay"])
    turns = transcriber.transcribe_file(wav, speaker="Me")
    assert seen["url"].startswith("https://api.meta.ai/v1/asr/transcribe")
    assert seen["auth"] == "Bearer LLM_test"
    assert b"muse-voice-transcribe-1.0" in seen["body"]
    assert b"Tagalog" in seen["body"]
    assert b"BayaniPay" in seen["body"]
    assert turns[0].speaker == "Me"
    assert turns[0].vendor_speaker == "B"
    assert turns_from_file_payload({"transcript": "solo", "audioDurationMs": 10, "turns": []}, "Them")[0].text == "solo"


def test_muse_spark_request_shape(tmp_path: Path):
    scenario = load_scenario()
    store = MeetingStore(tmp_path)
    pipeline = build_pipeline(
        store,
        scenario,
        settings(),
        transcriber_mode="scripted",
        spark_mode="rules",
        segmenter_mode="fallback",
    )
    pipeline.ingest_image(Image.open(scenario.frames[1].path))
    utterance = store.add_utterance(
        speaker="Them",
        text="hello",
        start_ms=0,
        end_ms=10,
        finished_at=0,
    )
    request = build_spark_request(
        model="muse-spark-1.3",
        prd_text=scenario.prd_text,
        utterances=[utterance],
        trigger=utterance,
        frame=store.latest_frame(),
    )
    assert request["model"] == "muse-spark-1.3"
    assert request["response_format"]["type"] == "json_schema"
    assert request["reasoning_effort"] == "minimal"
    assert request["max_completion_tokens"] >= 1500
    parts = request["messages"][1]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert str(http_request.url) == "https://api.meta.ai/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "cards": [
                                        {
                                            "type": "SUGGESTED_REPLY",
                                            "title": "Say this",
                                            "body": "Sige, i-note ko muna.",
                                            "prd_section": None,
                                            "who": None,
                                            "what": None,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    spark = MuseSpark(settings("LLM_test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    drafts = spark.review(prd_text="prd", utterances=[utterance], trigger=utterance, frame=None)
    assert drafts[0].type == "SUGGESTED_REPLY"


def test_parse_spark_rejects_empty_choices():
    with pytest.raises(Exception):
        parse_spark_response({"choices": []})


def test_sam_segmenter_crops_the_returned_box():
    image = Image.new("RGB", (100, 100), (0, 0, 80))
    image.paste(Image.new("RGB", (20, 16), (255, 196, 60)), (10, 8))
    body = "<0f>0<|box;x1=10;y1=8;x2=29;y2=23;w=100;h=100|>"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "sam-3.1"
        assert payload["stream"] is False
        assert str(request.url) == "https://api.meta.ai/v1/responses"
        content = payload["input"][0]["content"]
        assert content[0]["text"] == "percentage"
        assert content[1]["image_url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": body}]}]})

    sam = MuseSam(settings("LLM_test"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    hint = detect_deixis("yung nasa taas, the number")
    assert hint is not None
    crop = DeicticSegmenter(sam).crop(image, hint)
    assert crop.source == "sam-3.1"
    assert crop.image.getpixel((0, 0))[0] > 200


def test_device_selection_and_resample():
    devices = [
        {"index": 1, "name": "Speakers (Realtek)", "isLoopbackDevice": False},
        {"index": 4, "name": "Speakers (Realtek) [Loopback]", "isLoopbackDevice": True},
        {"index": 2, "name": "Headset Mic", "isLoopbackDevice": False},
    ]
    loopback = choose_loopback_device(devices, 1)
    assert loopback["index"] == 4
    assert choose_mic_device(devices, 2)["name"] == "Headset Mic"
    import array

    mono = array.array("h", [0x1000] * 480)
    stereo = array.array("h")
    for sample in mono:
        stereo.extend((sample, sample))
    converted, _state = pcm_to_mono_24k(stereo.tobytes(), 2, 2, 48000, None)
    assert abs(len(converted) / 2 - 240) <= 4


def test_panel_route_and_mcp_tools(tmp_path: Path):
    scenario = load_scenario()
    store = MeetingStore(tmp_path)
    state = AppState(store)
    state.pipeline = build_pipeline(
        store,
        scenario,
        settings(),
        transcriber_mode="scripted",
        spark_mode="rules",
        segmenter_mode="fallback",
    )
    run_replay(state.pipeline, scenario, pace_ms=0)
    server = create_server(state)
    cards = asyncio.run(server.call_tool("get_cards", {"limit": 20}))
    payload = json.loads(cards.content[0].text)
    assert {card["type"] for card in payload["cards"]} == {"PRD_CONFLICT", "COMMITMENT", "SUGGESTED_REPLY"}
    context = json.loads(asyncio.run(server.call_tool("get_live_context", {"window": 8})).content[0].text)
    assert context["transcript"]
    assert context["screen_deltas"]
    assert context["latency"]["p50_ms"] is not None
    assert context["meeting"]["prd_sections"]
    missed = json.loads(asyncio.run(server.call_tool("what_did_i_miss", {})).content[0].text)
    assert missed["open_items"]
    assert "summary" in missed
    assert "type" not in missed
    image = asyncio.run(server.call_tool("get_latest_frame", {}))
    assert image.content[0].type == "image"
    pushed = json.loads(
        asyncio.run(
            server.call_tool(
                "push_suggestion",
                {"text": "Sige, change request muna tayo.", "title": "Softer line"},
            )
        ).content[0].text
    )
    assert pushed["source"] == "assistant"
    assert pushed["type"] == "SUGGESTED_REPLY"
    assert pushed["latency_ms"] >= 0
    with pytest.raises(Exception):
        asyncio.run(server.call_tool("push_suggestion", {"text": "nope", "card_type": "NOT_A_CARD"}))
    app = server.streamable_http_app()
    with TestClient(app) as client:
        health = client.get("/panel/health")
        assert health.status_code == 200
        state_body = client.get("/panel/state").json()
        assert any(card["type"] == "PRD_CONFLICT" for card in state_body["cards"])
        media = state_body["latest_frame_url"].split("/")[-1]
        image_response = client.get(f"/panel/media/{media}")
        assert image_response.status_code == 200
        assert image_response.headers["content-type"].startswith("image/")
        assert client.get("/panel/media/../scenario.json").status_code == 404
        deleted = client.post("/panel/delete")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.get("/panel/state").json()["cards"] == []
