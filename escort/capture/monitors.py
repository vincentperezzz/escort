"""Which display Escort watches.

Monitors are identified by their mss index: `mss.mss().monitors[i]` for i >= 1 is one
physical display (Windows enumeration order, which is stable while the display setup
does not change) and index 0 is the bounding box of all of them. The user-facing name
is "Display i", so "monitor 2" in chat and "Display 2" in Settings are the same thing.

The saved choice (`watch_monitor`) is "primary", "all", or an int index. A saved index
that no longer exists falls back to the primary display with a one-line notice.
"""

from __future__ import annotations

from typing import Any, Callable

Choice = Any  # "primary" | "all" | int

PRIMARY = "primary"
ALL = "all"
ALL_INDEX = 0


def enumerate_raw() -> list[dict[str, int]]:
    """Raw mss monitor rects (index 0 = all displays). Seam for tests: monkeypatch this."""
    import mss

    with mss.mss() as sct:
        return [dict(item) for item in sct.monitors]


def _is_primary(rect: dict[str, int]) -> bool:
    left, top = int(rect.get("left", 0)), int(rect.get("top", 0))
    return left <= 0 < left + int(rect.get("width", 0)) and top <= 0 < top + int(rect.get("height", 0))


def describe(raw: list[dict[str, int]]) -> list[dict[str, Any]]:
    """Physical displays (raw[1:]) with index, geometry, primary flag and labels."""
    displays = raw[1:]
    primary_found = any(_is_primary(rect) for rect in displays)
    out: list[dict[str, Any]] = []
    for offset, rect in enumerate(displays):
        index = offset + 1
        primary = _is_primary(rect) if primary_found else index == 1
        if primary and any(item["primary"] for item in out):
            primary = False
        width, height = int(rect.get("width", 0)), int(rect.get("height", 0))
        name = f"Display {index}"
        out.append(
            {
                "index": index,
                "name": name,
                "label": f"{name} · {width}×{height}",
                "left": int(rect.get("left", 0)),
                "top": int(rect.get("top", 0)),
                "width": width,
                "height": height,
                "primary": primary,
            }
        )
    return out


def list_monitors(lister: Callable[[], list[dict[str, int]]] | None = None) -> list[dict[str, Any]]:
    try:
        return describe((lister or enumerate_raw)())
    except Exception:
        return []


def normalize_choice(value: Any) -> Choice:
    """'primary' | 'all' | int >= 1. Raises ValueError for anything else."""
    if isinstance(value, bool):
        raise ValueError("selected must be 'primary', 'all' or a display number")
    if isinstance(value, int):
        if value >= 1:
            return value
        raise ValueError("display numbers start at 1")
    text = str(value if value is not None else "").strip().lower()
    if text in {PRIMARY, "main", "default", ""}:
        return PRIMARY
    if text in {ALL, "all displays", "every"}:
        return ALL
    if text.isdigit() and int(text) >= 1:
        return int(text)
    raise ValueError("selected must be 'primary', 'all' or a display number")


def primary_of(monitors: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((item for item in monitors if item["primary"]), monitors[0] if monitors else None)


def resolve(choice: Choice, monitors: list[dict[str, Any]]) -> dict[str, Any]:
    """What a choice means right now: {selected, index, label, notice}.

    `index` is the mss index to grab (0 = all displays). `label` is what the UI
    shows after "Watching". `notice` is set when a saved display has gone away.
    """
    primary = primary_of(monitors)
    primary_index = primary["index"] if primary else 1
    primary_name = primary["name"] if primary else "Display 1"
    if choice == ALL:
        if len(monitors) <= 1:
            return {"selected": ALL, "index": primary_index, "label": primary_name, "notice": None}
        return {"selected": ALL, "index": ALL_INDEX, "label": "All displays", "notice": None}
    if isinstance(choice, int) and not isinstance(choice, bool):
        match = next((item for item in monitors if item["index"] == choice), None)
        if match is not None:
            return {"selected": choice, "index": choice, "label": match["name"], "notice": None}
        if not monitors:
            # Nothing enumerated (no mss yet): trust the saved index, the grabber clamps it.
            return {"selected": choice, "index": choice, "label": f"Display {choice}", "notice": None}
        notice = f"Display {choice} isn't connected any more, so Escort is watching {primary_name} (primary)."
        return {"selected": PRIMARY, "index": primary_index, "label": primary_name, "notice": notice}
    return {"selected": PRIMARY, "index": primary_index, "label": primary_name, "notice": None}


def option_list(monitors: list[dict[str, Any]]) -> str:
    """'1 · Display 1 (primary)  2 · Display 2' for chat questions."""
    parts = [f"{item['index']} · {item['name']}{' (primary)' if item['primary'] else ''}" for item in monitors]
    return "\n".join(parts)


def rects_overlap(rect: tuple[int, int, int, int], monitor: dict[str, Any]) -> bool:
    left, top, right, bottom = rect
    m_left, m_top = monitor["left"], monitor["top"]
    m_right, m_bottom = m_left + monitor["width"], m_top + monitor["height"]
    return left < m_right and right > m_left and top < m_bottom and bottom > m_top
