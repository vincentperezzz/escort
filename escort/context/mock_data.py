from __future__ import annotations

from pathlib import Path

from escort.context.models import CalendarEvent, ContextItem, ContextSection
from escort.paths import repo_root


def bayanipay_markdown() -> str:
    path = repo_root() / "fixtures" / "prd" / "bayanipay.md"
    return path.read_text(encoding="utf-8")


def mock_calendar() -> list[CalendarEvent]:
    return [
        CalendarEvent(
            id="evt_bayani_scope",
            title="BayaniPay scope review — Phase 1 vs Phase 2",
            start="2026-11-02T09:00:00+08:00",
            end="2026-11-02T10:00:00+08:00",
            description="Walk through merchant console, KYC, and whether payment integration stays Phase 2.",
            attendees=["boss@example.com", "client@bayanipay.demo"],
            linked_doc_hints=["SOW", "bayanipay", "Phase 2"],
            mock=True,
            meeting_url="https://meet.google.com/bay-anip-ayx",
        ),
        CalendarEvent(
            id="evt_weekly_sync",
            title="Weekly product sync",
            start="2026-11-03T15:00:00+08:00",
            end="2026-11-03T15:30:00+08:00",
            description="Standup only.",
            attendees=["boss@example.com"],
            linked_doc_hints=[],
            mock=True,
        ),
    ]


def mock_notion_hits() -> list[dict]:
    text = bayanipay_markdown()
    return [
        {
            "id": "notion_phase_scope",
            "title": "Phase scope — BayaniPay launch",
            "snippet": "Phase 2 covers card checkout and InstaPay cash-in.",
            "mock": True,
        },
        {
            "id": "notion_pricing",
            "title": "Pricing guardrails",
            "snippet": "Processing fee stays 2.4% until finance signs off.",
            "mock": True,
        },
    ]


def mock_import_item(source_kind: str, external_id: str) -> ContextItem:
    text = bayanipay_markdown()
    from escort.context.chunk import sections_from_markdown
    from escort.store import new_id

    labels = {
        "notion": "Notion",
        "google_docs": "Google Docs",
    }
    titles = {
        "notion_phase_scope": "Phase scope — BayaniPay launch",
        "notion_pricing": "Pricing guardrails",
        "docs_sow": "BayaniPay SOW (fictional)",
        "docs_kyc": "KYC checklist",
    }
    title = titles.get(external_id, external_id)
    return ContextItem(
        id=new_id("ctx"),
        source_kind=source_kind,
        source_label=labels.get(source_kind, source_kind.title()),
        title=title,
        sections=sections_from_markdown(text if "sow" in external_id or "scope" in external_id else text[:1200]),
        enabled=True,
        external_id=external_id,
        mock=True,
    )


def mock_google_link(kind: str, file_id: str) -> ContextItem:
    """Demo mode: a pasted Sheets or Slides link resolves to fictional BayaniPay content, labeled mock."""
    from escort.context.chunk import sections_from_markdown
    from escort.store import new_id

    if kind == "docs":
        return mock_google_doc(file_id)
    if kind == "sheets":
        source_kind, label, title = "google_sheets", "Google Sheets", "BayaniPay pricing sheet (demo)"
        markdown = (
            "# BayaniPay pricing sheet (demo)\n\n## Fees\n\n| Item | Rate | Owner |\n| --- | --- | --- |\n"
            "| Processing | 2.4% | Finance |\n| InstaPay cash-in | Free | Product |\n\n"
            "## Phases\n\n| Phase | Scope |\n| --- | --- |\n| 1 | Merchant console, KYC |\n| 2 | Card checkout, InstaPay |\n"
        )
    else:
        source_kind, label, title = "google_slides", "Google Slides", "BayaniPay kickoff deck (demo)"
        markdown = (
            "# BayaniPay kickoff deck (demo)\n\n## Slide 1: BayaniPay launch\n\nPhase 1 vs Phase 2\n\n"
            "## Slide 2: Phase 2 scope\n\nCard checkout\nInstaPay cash-in\n\nSpeaker notes:\nShips after KYC go-live.\n"
        )
    return ContextItem(
        id=new_id("ctx"),
        source_kind=source_kind,
        source_label=label,
        title=title,
        sections=sections_from_markdown(markdown),
        enabled=True,
        external_id=file_id,
        mock=True,
    )


def mock_drive_hits() -> list[dict]:
    return [
        {
            "id": "docs_sow",
            "title": "BayaniPay SOW (demo)",
            "snippet": "Google Doc",
            "mimeType": "application/vnd.google-apps.document",
            "modified": "2026-11-01T12:00:00.000Z",
            "mock": True,
        },
        {
            "id": "docs_kyc",
            "title": "KYC checklist (demo)",
            "snippet": "Google Doc",
            "mimeType": "application/vnd.google-apps.document",
            "modified": "2026-10-28T09:00:00.000Z",
            "mock": True,
        },
    ]


def mock_drive_file(file_id: str, name: str = "") -> ContextItem:
    """Demo mode: a picked Drive file becomes the fictional BayaniPay doc, labeled mock."""
    item = mock_import_item("google_drive", "docs_sow")
    item.source_label = "Google Drive"
    item.title = f"{name or 'Picked file'} (demo)"
    item.external_id = file_id
    return item


def mock_google_doc(doc_id: str) -> ContextItem:
    """Demo mode: a pasted Google Docs link resolves to a fictional BayaniPay doc, labeled mock."""
    known = doc_id in {"docs_sow", "docs_kyc"}
    item = mock_import_item("google_docs", doc_id if known else "docs_sow")
    item.external_id = doc_id
    if not known:
        item.title = "Pasted Google Doc (demo)"
    return item
