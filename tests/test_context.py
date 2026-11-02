from io import BytesIO
from pathlib import Path

import pytest

from escort.context.extract import extract_text
from escort.context.library import ContextLibrary
from escort.context.matcher import match_event
from escort.context.mock_data import mock_calendar
from escort.context.models import ContextItem, ContextSection
from escort.desktop.runtime import DesktopRuntime
from escort.store import MeetingStore
from fastapi.testclient import TestClient

from escort.desktop.api import create_desktop_app
from escort.desktop.secret_store import MemorySecretStore


def _library(tmp_path: Path) -> ContextLibrary:
    return ContextLibrary(tmp_path, lambda _key: None)


def test_extract_md_and_chunk(tmp_path: Path):
    lib = _library(tmp_path)
    item = lib.add_upload("scope.md", b"## 2.2 Phase 2\nPayment ships later.\n")
    assert item.sections
    assert item.sections[0].anchor == "2.2"


def test_matcher_scores_bayani_event(tmp_path: Path):
    lib = _library(tmp_path)
    lib.add_paste("# BayaniPay\n\n### 2.2 Phase 2 — Payment integration\nLater.\n", title="Scope")
    event = mock_calendar()[0]
    hits = match_event(event, lib.items())
    assert hits
    assert hits[0].score >= 3


def test_apply_to_store_sets_source_labels(tmp_path: Path):
    store = MeetingStore(tmp_path / "media")
    lib = _library(tmp_path)
    lib.add_item(
        ContextItem(
            id="ctx_test",
            source_kind="notion",
            source_label="Notion",
            title="Phase scope",
            sections=[ContextSection(anchor="2.2", heading="Phase 2", quote="Later", body="Later")],
        )
    )
    lib.apply_to_store(store)
    assert store.prd_sections[0]["source_label"] == "Notion · Phase 2"


def test_desktop_upload_and_match(tmp_path: Path):
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")
    client = TestClient(create_desktop_app(runtime))
    uploaded = client.post(
        "/api/context/upload",
        files={
            "file": (
                "bayanipay.md",
                b"## 2.2 Phase 2 - Payment integration\nPayment ships after KYC.\n",
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["context"]["items"]
    matched = client.post("/api/context/match", json={"event_id": "evt_bayani_scope"})
    assert matched.status_code == 200
    assert matched.json()["proposals"]


def test_pdf_extract_requires_pypdf():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        pytest.skip("pypdf not installed")
    text = extract_text("sample.pdf", b"%PDF-1.4\n")
    assert text == ""


def test_context_chat_passes_live_transcript_cards_and_screen_frame(tmp_path: Path, monkeypatch):
    import json
    import time
    import httpx
    from PIL import Image
    from escort.asr_text import correct_names
    from escort.context import prep_chat as prep_chat_mod
    from escort.prd import keywords_from_prd

    # ASR name correction and keyword extraction from loaded notes
    notes_md = "## 2.1 Stakeholders\nMaria Santos and Marco Villanueva lead BayaniPay Phase 1.\n"
    kw = keywords_from_prd(notes_md)
    assert any("Maria" in item for item in kw)
    assert correct_names("Marria asked if Vinsent can join", ["Maria", "Vincent"]) == "Maria asked if Vincent can join"

    seen: list[dict] = []

    def fake_muse(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Maria asked to pull QR payments into Phase 1, which conflicts with Section 2.2."}}]})

    real_client = httpx.Client
    monkeypatch.setattr(prep_chat_mod.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(fake_muse)))
    runtime = DesktopRuntime(MeetingStore(tmp_path / "media"), MemorySecretStore(), tmp_path / "config.json")
    runtime.secrets.set("meta_api_key", "LLM_test_key")
    runtime.context.add_paste(notes_md, title="Scope Notes")
    runtime.store.add_utterance(
        speaker="Them",
        text="Can we move QR checkout into Phase 1?",
        start_ms=0,
        end_ms=1000,
        finished_at=time.perf_counter(),
        vendor_speaker="A",
    )
    runtime.store.set_partial("Them", "Also Maria on the slide wants...")
    frame_img = Image.new("RGB", (120, 80), (30, 40, 60))
    frame_path = runtime.store.save_png(frame_img, "frame")
    runtime.store.add_frame(path=frame_path, phash="abcd")

    out = runtime.context_chat("What did she say and how should I respond?")
    assert "Maria asked" in out["reply"]
    assert seen
    req = seen[-1]
    sys_prompt = req["messages"][0]["content"]
    assert "Respond in clear, natural English by default" in sys_prompt
    assert "Do NOT default to Taglish" in sys_prompt
    assert "muse-spark-1.3" in sys_prompt
    user_parts = req["messages"][1]["content"]
    assert isinstance(user_parts, list)
    assert user_parts[1]["type"] == "image_url"
    text_part = user_parts[0]["text"]
    assert "Them [Speaker A]: Can we move QR checkout into Phase 1?" in text_part
    assert "Them (speaking right now): Also Maria on the slide wants..." in text_part


def test_no_notes_meeting_takes_notes_and_flags_intra_meeting_conflict_and_grabs_frame(tmp_path):
    import json

    from PIL import Image

    from escort.desktop.live_capture import grab_chat_frame
    from escort.models import Turn
    from escort.pipeline import Pipeline
    from escort.spark.muse import parse_spark_response
    from escort.spark.rules import RuleSpark
    from escort.store import MeetingStore
    from escort.transcribe.scripted import ScriptedTranscriber

    store = MeetingStore(tmp_path / "media")
    # Start a meeting with NO prior notes loaded (prd_text="")
    pipeline = Pipeline(store, ScriptedTranscriber({}), RuleSpark(), None, prd_text="")

    # First part of the meeting establishes baseline rate (2.4%) and English commitment
    pipeline.finish_turn(Turn(speaker="Me", text="For Phase 1 the processing fee is 2.4% and I'll send the updated deck after this call.", start_ms=0, end_ms=1000))
    # Later part of the meeting contradicts the earlier 2.4% agreement
    pipeline.finish_turn(Turn(speaker="Them", text="Let's adjust the processing fee to 2.9% instead.", start_ms=1100, end_ms=2000))

    card_types = [c.type for c in store.cards]
    assert "COMMITMENT" in card_types
    assert "PRD_CONFLICT" in card_types
    conflict = next(c for c in store.cards if c.type == "PRD_CONFLICT")
    assert conflict.prd_section == "Earlier in this call"
    assert "2.4%" in conflict.body

    # Muse Spark parser also preserves intra-meeting conflicts when prd_section is null
    parsed = parse_spark_response(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "cards": [
                                    {
                                        "type": "PRD_CONFLICT",
                                        "title": "Timeline shifted from Friday to Monday",
                                        "body": "Earlier in the call you agreed on Friday delivery.",
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
        }
    )
    assert len(parsed) == 1
    assert parsed[0].prd_section == "Earlier in this call"

    # On-demand chat screen grabber captures a fresh frame when chatting
    class FakeGrabber:
        def set_monitor(self, monitor: int) -> None:
            self.monitor = monitor

        def grab(self):
            return Image.new("RGB", (64, 48), (10, 20, 30))

        def close(self) -> None:
            pass

    frame = grab_chat_frame(store, monitor_index=1, grabber_factory=FakeGrabber)
    assert frame is not None
    assert store.latest_frame() == frame
