from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import httpx

from escort.context.models import CalendarEvent, Project
from escort.settings import Settings
from escort.spark.muse import spark_model_name

ConfidenceFn = Callable[[CalendarEvent, Project, list[str]], float | None]


def _tokens(text: str) -> set[str]:
    return {part.lower() for part in re.findall(r"[a-z0-9]{3,}", text.lower())}


def fallback_match_event(
    event: CalendarEvent,
    projects: list[Project],
    titles_by_project: dict[str, list[str]],
    min_score: float = 2.0,
) -> tuple[str | None, float]:
    hay = " ".join([event.title, event.description, *event.attendees]).lower()
    event_tokens = _tokens(hay)
    best_id: str | None = None
    best_score = 0.0
    for proj in projects:
        blob = " ".join([proj.name, proj.description, *titles_by_project.get(proj.id, [])])
        proj_tokens = _tokens(blob)
        overlap = event_tokens & proj_tokens
        score = float(len(overlap))
        name_lower = proj.name.lower()
        if name_lower and name_lower in event.title.lower():
            score += 3.0
        for token in proj_tokens:
            if len(token) >= 5 and token in hay:
                score += 0.5
        if score > best_score:
            best_score = score
            best_id = proj.id
    if best_id is None or best_score < min_score:
        return None, 0.0
    return best_id, best_score


def match_events_with_ai(
    events: list[CalendarEvent],
    projects: list[Project],
    titles_by_project: dict[str, list[str]],
    settings: Settings,
    http: httpx.Client | None = None,
    confidence_fn: ConfidenceFn | None = None,
) -> dict[str, str | None]:
    if not projects or not events:
        return {event.id: None for event in events}
    if not settings.api_key:
        out: dict[str, str | None] = {}
        for event in events:
            pid, _score = fallback_match_event(event, projects, titles_by_project)
            out[event.id] = pid
        return out
    if confidence_fn is not None:
        out = {}
        for event in events:
            best_id: str | None = None
            best = 0.0
            for proj in projects:
                score = confidence_fn(event, proj, titles_by_project.get(proj.id, []))
                if score is None:
                    continue
                if score > best:
                    best = score
                    best_id = proj.id
            out[event.id] = best_id if best >= 0.55 else None
        return out
    payload = _build_prompt(events, projects, titles_by_project)
    model_id = spark_model_name(settings.spark_model)
    client = http or httpx.Client(timeout=60)
    owns = http is None
    try:
        response = client.post(
            f"{settings.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
            json={
                "model": model_id,
                "reasoning_effort": "minimal",
                "max_completion_tokens": 1200,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You link calendar meetings to workspace projects. "
                            "Reply with JSON only: {\"assignments\":[{\"event_id\":\"...\",\"project_id\":\"...\"|null,\"confidence\":0-1}]}. "
                            "Use null project_id when not confident (confidence below 0.55). Never guess."
                        ),
                    },
                    {"role": "user", "content": payload},
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        return _parse_assignments(str(content).strip(), events, projects)
    except Exception:
        out = {}
        for event in events:
            pid, _score = fallback_match_event(event, projects, titles_by_project)
            out[event.id] = pid
        return out
    finally:
        if owns:
            client.close()


def _build_prompt(
    events: list[CalendarEvent],
    projects: list[Project],
    titles_by_project: dict[str, list[str]],
) -> str:
    proj_lines = []
    for proj in projects:
        titles = titles_by_project.get(proj.id) or []
        title_blob = ", ".join(titles[:12]) if titles else "(no files yet)"
        proj_lines.append(
            f"- id={proj.id} name={proj.name!r} description={proj.description!r} file_titles={title_blob}"
        )
    event_lines = []
    for event in events:
        attendees = ", ".join(event.attendees[:8])
        event_lines.append(
            f"- id={event.id} title={event.title!r} start={event.start} "
            f"description={event.description!r} attendees={attendees}"
        )
    return "PROJECTS:\n" + "\n".join(proj_lines) + "\n\nEVENTS:\n" + "\n".join(event_lines)


def _parse_assignments(
    text: str,
    events: list[CalendarEvent],
    projects: list[Project],
) -> dict[str, str | None]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    data = json.loads(cleaned)
    valid_projects = {p.id for p in projects}
    event_ids = {e.id for e in events}
    out: dict[str, str | None] = {eid: None for eid in event_ids}
    for row in data.get("assignments") or []:
        if not isinstance(row, dict):
            continue
        eid = str(row.get("event_id") or "")
        if eid not in event_ids:
            continue
        confidence = float(row.get("confidence") or 0.0)
        pid = row.get("project_id")
        if pid is None or confidence < 0.55:
            out[eid] = None
            continue
        pid_str = str(pid)
        out[eid] = pid_str if pid_str in valid_projects else None
    return out


def assignments_to_project_events(
    assignments: dict[str, str | None],
    events_by_id: dict[str, CalendarEvent],
) -> dict[str, dict[str, CalendarEvent | None]]:
    by_project: dict[str, dict[str, CalendarEvent | None]] = {}
    for event_id, project_id in assignments.items():
        if project_id is None:
            continue
        by_project.setdefault(project_id, {})[event_id] = events_by_id.get(event_id)
    return by_project
