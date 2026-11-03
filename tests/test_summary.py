"""Post-meeting summary: uncapped highlights, new sections, map-reduce for long meetings, exports."""

from __future__ import annotations

import json

import httpx

from escort.context.sources.google_docs import summary_blocks
from escort.context.sources.notion_write import summary_children
from escort.settings import Settings
from escort.summary import (
    MAX_COMPLETION_TOKENS,
    MIN_COMPLETION_TOKENS,
    MAX_TRANSCRIPT_CHARS,
    build_summary_request,
    chunk_transcript,
    empty_summary,
    generate_summary,
    parse_summary_response,
    rules_summary,
)


def _settings(key: str | None = "test-key") -> Settings:
    return Settings(
        api_key=key,
        base_url="https://api.meta.ai/v1",
        asr_model="muse-voice-transcribe-1.0",
        spark_model="muse-spark-1.3",
        sam_model="sam-3.1",
        language_bias=("Tagalog", "English"),
        spark_interval_s=10,
    )


def _snapshot(turns: list[dict] | None = None, **extra) -> dict:
    meeting = {
        "turns": turns if turns is not None else [{"speaker": "Them", "text": "Can we add QR payments to Phase 1?"}],
        "commitments": [
            {"who": "Me", "what": "send the revised estimate", "when": "Friday"},
            {"who": "Them", "what": "share the KYC vendor list"},
        ],
        "open_items": [],
    }
    return {"title": "Scope call", "meeting": meeting, "cards": [], **extra}


def _reply(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


FINAL = {
    "title": "Scope call",
    "bullets": ["Client wants QR in Phase 1."],
    "takeaways": ["Phase 1 timeline is at risk."],
    "decisions": ["KYC stays in Phase 2."],
    "open_questions": ["Who owns the QR provider contract?"],
    "commitments": [],
    "conflicts": [],
}


def test_prompt_has_no_bullet_cap_and_asks_for_new_fields():
    request = build_summary_request(_settings(), _snapshot(), "")
    system = request["messages"][0]["content"]
    assert "3-6" not in system
    assert "as many as the content genuinely warrants" in system
    assert "takeaways" in system and "decisions" in system and "open_questions" in system
    schema = request["response_format"]["json_schema"]["schema"]
    assert {"takeaways", "decisions", "open_questions", "bullets", "commitments", "conflicts", "title"} <= set(schema["required"])
    assert request["max_completion_tokens"] == MIN_COMPLETION_TOKENS
    assert "VISUALS SHOWN" not in request["messages"][1]["content"]


def test_token_budget_scales_with_transcript():
    long_turns = [{"speaker": "Them", "text": "x" * 500} for _ in range(400)]
    request = build_summary_request(_settings(), _snapshot(long_turns), "")
    assert request["max_completion_tokens"] == MAX_COMPLETION_TOKENS


def test_visual_captions_are_included():
    shots = [
        {"id": "s1", "url": "/snap/1.png", "caption": "Org chart of the payments team", "kind": "diagram", "captured_at": "10:02"},
        {"id": "s2", "url": "/snap/2.png", "caption": "", "kind": "chart"},
    ]
    content = build_summary_request(_settings(), _snapshot(snapshots=shots), "")["messages"][1]["content"]
    assert "VISUALS SHOWN" in content
    assert "[diagram at 10:02] Org chart of the payments team" in content


def test_parse_reads_new_fields_and_tolerates_old_responses():
    parsed = parse_summary_response({"choices": [{"message": {"content": json.dumps(FINAL)}}]})
    assert parsed["takeaways"] == ["Phase 1 timeline is at risk."]
    assert parsed["decisions"] == ["KYC stays in Phase 2."]
    assert parsed["open_questions"] == ["Who owns the QR provider contract?"]
    old = {"title": "t", "bullets": ["a", "b", "c", "d", "e", "f", "g", "h"], "commitments": [], "conflicts": []}
    parsed = parse_summary_response({"choices": [{"message": {"content": json.dumps(old)}}]})
    assert len(parsed["bullets"]) == 8
    assert parsed["takeaways"] == parsed["decisions"] == parsed["open_questions"] == []


def test_short_meeting_is_one_call_with_new_fields():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _reply(FINAL)

    result = generate_summary(_settings(), _snapshot(), "", httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(seen) == 1
    assert result["source"] != "rules" and result["decisions"] == ["KYC stays in Phase 2."]
    assert result["takeaways"] == ["Phase 1 timeline is at risk."]


def test_long_meeting_is_chunked_into_segment_notes_then_final():
    turns = [{"speaker": "Them" if i % 2 else "Me", "text": f"turn {i:04d} " + "word " * 60} for i in range(300)]
    full = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)
    assert len(full) > MAX_TRANSCRIPT_CHARS
    chunks = chunk_transcript(full.split("\n"))
    assert len(chunks) >= 2 and "\n".join(chunks) == full, "chunks split on turn boundaries and keep everything"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["response_format"]["json_schema"]["name"] == "MeetingSegmentNotes":
            n = len(seen)
            return _reply({"highlights": [f"Segment highlight {n}"], "decisions": [], "commitments": [], "open_questions": []})
        return _reply(FINAL)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = generate_summary(_settings(), _snapshot(turns), "", client)
    assert len(seen) == len(chunks) + 1
    assert all(b["response_format"]["json_schema"]["name"] == "MeetingSegmentNotes" for b in seen[:-1])
    assert f"PART 2 OF {len(chunks)}" in seen[1]["messages"][1]["content"]
    final = seen[-1]["messages"][1]["content"]
    assert "SEGMENT NOTES" in final and "Segment highlight 1" in final and f"Part {len(chunks)} of {len(chunks)}" in final
    assert "turn 0000" not in final, "the start of the meeting arrives as notes, not raw transcript"
    assert "turn 0299" in final, "the raw tail is kept for recency"
    assert len(final) < len(full)
    assert result["bullets"] == FINAL["bullets"] and result["error"] is None


def test_chunk_failure_falls_back_to_rules():
    turns = [{"speaker": "Them", "text": "word " * 100} for _ in range(100)]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500) if len(calls) == 2 else _reply({"highlights": [], "decisions": [], "commitments": [], "open_questions": []})

    result = generate_summary(_settings(), _snapshot(turns), "", httpx.Client(transport=httpx.MockTransport(handler)))
    assert result["source"] == "rules" and "Muse summary failed" in result["error"]
    assert len(calls) == 2


def test_rules_fallback_sets_new_fields():
    result = rules_summary(_snapshot())
    assert result["takeaways"] == [] and result["decisions"] == []
    assert result["open_questions"] == ["When will Them share the KYC vendor list? No date was set."]
    assert set(empty_summary()) >= {"takeaways", "decisions", "open_questions"}
    assert generate_summary(_settings(None), _snapshot())["source"] == "rules"


def test_exports_emit_new_sections_only_when_non_empty():
    base = {"bullets": ["One."], "commitments": [], "conflicts": []}
    assert [line for line, style in summary_blocks(base, "x") if style == "HEADING_2"] == ["Summary", "Commitments", "Conflicts"]
    assert [b["heading_3"]["rich_text"][0]["text"]["content"] for b in summary_children(base, "x") if b["type"] == "heading_3"] == [
        "Summary",
        "Commitments",
        "Conflicts",
    ]
    full = {**base, "takeaways": ["Risk."], "decisions": ["Ship v1."], "open_questions": ["Who signs?"]}
    blocks = summary_blocks(full, "x")
    assert [line for line, style in blocks if style == "HEADING_2"] == [
        "Summary",
        "Key takeaways",
        "Decisions",
        "Open questions",
        "Commitments",
        "Conflicts",
    ]
    assert "- Ship v1." in [line for line, _ in blocks]
    children = summary_children(full, "x")
    headings = [b["heading_3"]["rich_text"][0]["text"]["content"] for b in children if b["type"] == "heading_3"]
    assert headings == ["Summary", "Key takeaways", "Decisions", "Open questions", "Commitments", "Conflicts"]


def test_jots_are_the_skeleton_of_the_notes():
    from escort.summary import build_summary_request, parse_summary_response, rules_summary

    snapshot = {"jots": [{"text": "RBAC: reps can't export", "after": "Jessa: only admins export leads"}], "meeting": {"turns": []}}
    from escort.settings import load_settings

    request = build_summary_request(load_settings(), snapshot, "")
    text = request["messages"][1]["content"]
    assert "MY NOTES (jotted by the user" in text and "RBAC: reps can't export" in text and "only admins export leads" in text
    assert "from_you=true" in request["messages"][0]["content"]
    rules = rules_summary(snapshot)
    assert rules["notes"] == [{"heading": "Your notes", "points": [{"text": "RBAC: reps can't export", "from_you": True, "sub": []}]}]
    payload = {"choices": [{"message": {"content": json.dumps({
        "title": "t", "meeting_type": "client_call", "bullets": ["b"], "takeaways": [], "decisions": [], "open_questions": [],
        "commitments": [], "conflicts": [],
        "notes": [{"heading": "RBAC roles", "points": [{"text": "RBAC: reps can't export", "from_you": True, "sub": ["Admins only (Jessa)"]}, {"text": "", "from_you": False, "sub": []}]}],
    })}}]}
    parsed = parse_summary_response(payload)
    assert parsed["meeting_type"] == "client_call"
    assert parsed["notes"] == [{"heading": "RBAC roles", "points": [{"text": "RBAC: reps can't export", "from_you": True, "sub": ["Admins only (Jessa)"]}]}]


def test_notes_export_to_docs_and_notion():
    from escort.context.sources.google_docs import summary_blocks
    from escort.context.sources.notion_write import summary_children

    summary = {"bullets": ["b"], "notes": [{"heading": "RBAC roles", "points": [{"text": "Reps can't export", "from_you": True, "sub": ["Admins only"]}]}]}
    docs = summary_blocks(summary, "now")
    assert ("Notes", "HEADING_2") in docs and ("RBAC roles", "HEADING_3") in docs and ("    - Admins only", "NORMAL_TEXT") in docs
    notion = [b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in summary_children(summary, "now") if b["type"] == "bulleted_list_item"]
    assert "Reps can't export" in notion and "↳ Admins only" in notion
