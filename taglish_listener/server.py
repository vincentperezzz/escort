from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from taglish_listener.pipeline import Pipeline
from taglish_listener.store import MeetingStore


class AppState:
    def __init__(self, store: MeetingStore) -> None:
        self.store = store
        self.pipeline: Pipeline | None = None


def create_server(state: AppState) -> MCPServer:
    mcp = MCPServer(
        "taglish-listener",
        instructions=(
            "Taglish scope guard. The product is one meeting's live state, not a recording. "
            "PRD conflicts are pushed onto the panel without a query, with the PRD section and a screen crop. "
            "get_live_context returns that state plus latency p50 and p95. "
            "get_cards returns only PRD_CONFLICT, COMMITMENT, and SUGGESTED_REPLY. "
            "push_suggestion is how a card lands on the panel. "
            "what_did_i_miss is a short side summary, not a fourth card. "
            "delete_meeting_state wipes turns, cards, and the last frame when the call is over. "
            "The bundled demo is a staged fictional BayaniPay meeting. Do not invent a PRD section."
        ),
    )

    @mcp.tool(structured_output=False)
    def get_live_context(window: int = 12) -> dict:
        """Meeting state: PRD sections, turns, commitments, open items, last frame, cards, and latency p50/p95."""
        snap = state.store.snapshot()
        size = max(1, min(window, 50))
        meeting = snap["meeting"]
        meeting["turns"] = meeting["turns"][-size:]
        return {
            "title": snap["title"],
            "mode": snap["mode"],
            "consent": snap["consent"],
            "retention": snap["retention"],
            "meeting": meeting,
            "latency": snap["latency"],
            "transcript": snap["utterances"][-size:],
            "partials": snap["partials"],
            "cards": snap["cards"][-size:],
            "screen_deltas": snap["frames"],
            "skipped_frames": snap["skipped_frames"],
            "warnings": snap["warnings"],
            "backends": {
                "transcriber": snap["transcriber"],
                "spark": snap["spark"],
                "segmenter": snap["segmenter"],
            },
        }

    @mcp.tool(structured_output=False)
    def get_latest_frame() -> Image:
        """Newest screen frame that passed the change detector, as an image."""
        frame = state.store.latest_frame()
        if frame is None:
            raise ToolError("No screen frame yet")
        path = Path(frame.path)
        if not path.is_file():
            raise ToolError("Latest frame file is missing")
        return Image(path=path)

    @mcp.tool(structured_output=False)
    def get_cards(limit: int = 20) -> dict:
        """Scope cards. Each one has type, timestamp, and latency_ms from the triggering utterance."""
        cards = state.store.snapshot()["cards"]
        size = max(1, min(limit, 100))
        return {"cards": cards[-size:]}

    @mcp.tool(structured_output=False)
    def push_suggestion(
        text: str,
        title: str = "Say this",
        card_type: str = "SUGGESTED_REPLY",
        prd_section: str | None = None,
        who: str | None = None,
        what: str | None = None,
        when: str | None = None,
        utterance_id: str | None = None,
    ) -> dict:
        """Push a card onto the live side panel without waiting to be asked. card_type is PRD_CONFLICT, COMMITMENT, or SUGGESTED_REPLY."""
        if state.pipeline is None:
            raise ToolError("Listener pipeline is not running")
        try:
            card = state.pipeline.push_suggestion(
                text,
                title=title,
                card_type=card_type,
                prd_section=prd_section,
                who=who,
                what=what,
                when=when,
                utterance_id=utterance_id,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return card.to_dict()

    @mcp.tool(structured_output=False)
    def what_did_i_miss() -> dict:
        """Short side summary of open PRD items and promises. This is not a fourth card type."""
        if state.pipeline is None:
            meeting = state.store.meeting()
            return {
                "summary": "Nothing open yet. No promises on the table.",
                "open_items": meeting["open_items"],
                "commitments": meeting["commitments"],
            }
        return state.pipeline.what_did_i_miss()

    @mcp.tool(structured_output=False)
    def delete_meeting_state() -> dict:
        """Delete this meeting's turns, cards, commitments, open items, and the last frame."""
        return state.store.delete_meeting()

    @mcp.custom_route("/panel/delete", methods=["POST"])
    async def panel_delete(_request: Request) -> Response:
        body = state.store.delete_meeting()
        response = JSONResponse(body)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @mcp.custom_route("/panel/health", methods=["GET"])
    async def panel_health(_request: Request) -> Response:
        return JSONResponse({"ok": True, "mode": state.store.mode})

    @mcp.custom_route("/panel/state", methods=["GET"])
    async def panel_state(_request: Request) -> Response:
        response = JSONResponse(state.store.snapshot())
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @mcp.custom_route("/panel/media/{name}", methods=["GET"])
    async def panel_media(request: Request) -> Response:
        path = state.store.resolve_media(request.path_params["name"])
        if path is None:
            return Response(status_code=404)
        return FileResponse(path)

    return mcp
