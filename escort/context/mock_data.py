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
        ),
        CalendarEvent(
            id="evt_weekly_sync",
            title="Weekly product sync",
            start="2026-11-03T15:00:00+08:00",
            end="2026-11-03T15:30:00+08:00",
            description="Standup only.",
            attendees=["boss@example.com"],
            linked_doc_hints=[],
        ),
    ]


def mock_notion_hits() -> list[dict]:
    text = bayanipay_markdown()
    return [
        {
            "id": "notion_phase_scope",
            "title": "Phase scope — BayaniPay launch",
            "snippet": "Phase 2 covers card checkout and InstaPay cash-in.",
        },
        {
            "id": "notion_pricing",
            "title": "Pricing guardrails",
            "snippet": "Processing fee stays 2.4% until finance signs off.",
        },
    ]


def mock_drive_hits() -> list[dict]:
    return [
        {"id": "drive_sow", "title": "BayaniPay SOW (fictional).pdf", "snippet": "Payment integration is explicitly Phase 2."},
        {"id": "drive_kyc", "title": "KYC checklist.docx", "snippet": "Identity check stays in Phase 1 onboarding."},
    ]


def mock_import_item(source_kind: str, external_id: str) -> ContextItem:
    text = bayanipay_markdown()
    from escort.context.chunk import sections_from_markdown
    from escort.store import new_id

    labels = {
        "notion": "Notion",
        "drive": "Drive",
    }
    titles = {
        "notion_phase_scope": "Phase scope — BayaniPay launch",
        "notion_pricing": "Pricing guardrails",
        "drive_sow": "BayaniPay SOW (fictional)",
        "drive_kyc": "KYC checklist",
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
    )
