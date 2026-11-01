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
from escort.desktop.secrets import MemorySecretStore


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
