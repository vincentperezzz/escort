from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from escort.store import new_id, utc_now

log = logging.getLogger("escort.context.sessions")

SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-]{4,128}$")
LEGACY_RECAP_NAMES = ("last_recap.json", "last-recap.json", "recap.json")


def _safe_id(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean or not SAFE_ID.match(clean) or ".." in clean or "/" in clean or "\\" in clean:
        raise KeyError(f"Invalid {label}: {value!r}")
    return clean


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_s(started_at: str | None, ended_at: str | None) -> int:
    start = _parse_ts(started_at)
    end = _parse_ts(ended_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds()))


def _summary_line(summary: dict[str, Any]) -> str:
    bullets = summary.get("bullets") or []
    if bullets:
        return str(bullets[0]).strip()
    title = str(summary.get("title") or "").strip()
    return title or "Meeting summary"


def _transcript_rows(utterances: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in utterances:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "t": str(item.get("created_at") or item.get("finished_at") or ""),
                "speaker": str(item.get("speaker") or "Them"),
                "text": text,
            }
        )
    return rows


class ProjectSessionStore:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.sessions_dir = project_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        sid = _safe_id(session_id, "session_id")
        root = self.sessions_dir.resolve()
        target = (root / f"{sid}.json").resolve()
        if target.parent != root:
            raise KeyError("Invalid session path")
        return target

    def list_summaries(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or not data.get("id"):
                continue
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            rows.append(
                {
                    "id": data["id"],
                    "title": str(data.get("title") or "Session"),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "duration_s": int(data.get("duration_s") or 0),
                    "project_id": data.get("project_id"),
                    "summary": data.get("summary_line") or _summary_line(summary),
                    "card_count": int(data.get("card_count") or 0),
                    "commitment_count": int(data.get("commitment_count") or 0),
                }
            )
        rows.sort(key=lambda row: str(row.get("ended_at") or row.get("started_at") or ""), reverse=True)
        return rows

    def get(self, session_id: str) -> dict[str, Any] | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def recap_payload(self, session_id: str) -> dict[str, Any] | None:
        data = self.get(session_id)
        if data is None:
            return None
        summary = data.get("recap") if isinstance(data.get("recap"), dict) else {}
        if not summary and isinstance(data.get("summary"), dict):
            summary = dict(data["summary"])
        transcript = data.get("transcript")
        if not isinstance(transcript, list):
            transcript = _transcript_rows(list(data.get("utterances") or []))
        full_summary = data.get("summary") if isinstance(data.get("summary"), dict) else dict(summary)
        payload = dict(summary)
        payload["summary"] = full_summary
        payload["transcript"] = transcript
        payload["id"] = data.get("id")
        payload["title"] = data.get("title")
        payload["started_at"] = data.get("started_at")
        payload["ended_at"] = data.get("ended_at")
        if data.get("cards") is not None:
            payload["cards"] = data.get("cards")
        if data.get("open_items") is not None:
            payload["open_items"] = data.get("open_items")
        if data.get("commitments") is not None:
            payload["commitments"] = data.get("commitments")
        if isinstance(data.get("meeting"), dict):
            payload.setdefault("meeting", data["meeting"])
        return payload

    def save(
        self,
        *,
        project_id: str,
        title: str,
        started_at: str,
        ended_at: str,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        meeting = snapshot.get("meeting") or {}
        cards = list(snapshot.get("cards") or [])
        utterances = list(snapshot.get("utterances") or meeting.get("turns") or [])
        commitments = meeting.get("commitments") or []
        open_items = meeting.get("open_items") or []
        card_count = len(cards)
        commitment_count = len([c for c in cards if c.get("type") == "COMMITMENT"]) or len(commitments)
        sid = session_id or new_id("sess")
        record = {
            "id": sid,
            "project_id": project_id,
            "title": title or "Session",
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": _duration_s(started_at, ended_at),
            "summary_line": _summary_line(summary),
            "summary": summary,
            "recap": summary,
            "card_count": card_count,
            "commitment_count": commitment_count,
            "transcript": _transcript_rows(utterances),
            "utterances": utterances,
            "cards": cards,
            "commitments": commitments,
            "open_items": open_items,
            "meeting": meeting,
        }
        path = self._path(sid)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def delete_all(self) -> int:
        count = 0
        for path in list(self.sessions_dir.glob("*.json")):
            try:
                path.unlink()
                count += 1
            except OSError:
                log.warning("Could not delete session file %s", path)
        return count


def migrate_legacy_recaps(base_dir: Path, project_dir: Path, project_id: str) -> int:
    migrated = 0
    store = ProjectSessionStore(project_dir)
    candidates: list[Path] = []
    for name in LEGACY_RECAP_NAMES:
        p = base_dir / name
        if p.is_file():
            candidates.append(p)
    legacy_dir = base_dir / "sessions"
    if legacy_dir.is_dir():
        candidates.extend(sorted(legacy_dir.glob("*.json")))
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else raw
        if not isinstance(summary, dict):
            continue
        started = str(raw.get("started_at") or summary.get("started_at") or utc_now())
        ended = str(raw.get("ended_at") or summary.get("generated_at") or utc_now())
        title = str(raw.get("title") or summary.get("title") or "Imported session")
        snapshot = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else raw
        store.save(
            project_id=project_id,
            title=title,
            started_at=started,
            ended_at=ended,
            summary=summary if summary.get("status") else {**summary, "status": "ready"},
            snapshot=snapshot if isinstance(snapshot, dict) else {"utterances": raw.get("transcript") or []},
            session_id=str(raw.get("id") or new_id("sess")),
        )
        migrated += 1
        try:
            if path.parent.name == "sessions" and path.name.endswith(".json"):
                path.unlink()
            elif path.name in LEGACY_RECAP_NAMES:
                path.unlink()
        except OSError:
            pass
    if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
        try:
            legacy_dir.rmdir()
        except OSError:
            pass
    return migrated


def count_chat_messages(project_dir: Path) -> int:
    context_path = project_dir / "context.json"
    if not context_path.is_file():
        return 0
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    chat = data.get("chat")
    return len(chat) if isinstance(chat, list) else 0


def count_context_items(project_dir: Path) -> int:
    context_path = project_dir / "context.json"
    if not context_path.is_file():
        return 0
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    items = data.get("items")
    return len(items) if isinstance(items, list) else 0


def count_upload_files(project_dir: Path) -> int:
    uploads = project_dir / "uploads"
    if not uploads.is_dir():
        return 0
    return sum(1 for p in uploads.iterdir() if p.is_file())
